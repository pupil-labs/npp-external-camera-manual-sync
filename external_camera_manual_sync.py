import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

import numpy as np
import pupil_labs.video as plv
from pupil_labs.neon_player import Plugin, action
from pupil_labs.neon_player.job_manager import (
    ProgressUpdate,
)
from pupil_labs.neon_player.utilities import (
    qimage_from_frame,
)
from pupil_labs.neon_recording import NeonRecording
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap, QTransform
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qt_property_widgets.utilities import action_params

logger = logging.getLogger(__name__)


class PTSExtractorWorker(QObject):
    progress = Signal(float)
    finished_extraction = Signal(object, object, float, int)
    error = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            self.progress.emit(0.1)
            reader = plv.Reader(self.path, stream="video")

            pts = reader.pts
            length = len(pts)

            self.progress.emit(0.9)

            if reader._stream.time_base is not None:
                tb = float(reader._stream.time_base)
            else:
                tb = 1.0 / float(getattr(reader, "average_rate", 30.0) or 30.0)

            self.progress.emit(1.0)
            self.finished_extraction.emit(reader, pts, tb, length)

        except Exception as e:
            self.error.emit(str(e))


def frame_to_qimage(frame: Any) -> QImage | None:
    try:
        if hasattr(frame, "bgr") and frame.bgr is not None:
            return qimage_from_frame(np.ascontiguousarray(frame.bgr))

    except Exception as e:
        logger.debug("Frame conversion to QImage failed: %s", e)

    return None


class SyncDockWidget(QDockWidget):
    def __init__(
        self, plugin: "ExternalCameraManualSyncPlugin", main_window: QMainWindow
    ):
        super().__init__("External Camera Manual Sync Tool", main_window)
        self.plugin = plugin

        self.ext_video_path: Path | None = None
        self.reader = None
        self.ext_frames_count = 0
        self.ext_pts: list[int] | None = None
        self.ext_time_base: float | None = None
        self._current_pixmap: QPixmap | None = None

        self._main_widget = QWidget()
        self.setWidget(self._main_widget)

        main_layout = QVBoxLayout()
        self._main_widget.setLayout(main_layout)

        self.btn_load_ext = QPushButton("Load Video...")
        self.btn_load_ext.clicked.connect(self._load_external_video)
        main_layout.addWidget(self.btn_load_ext)

        self.ext_img_label = QLabel("No video loaded")
        self.ext_img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ext_img_label.setStyleSheet("background-color: black; color: white;")
        self.ext_img_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        main_layout.addWidget(self.ext_img_label, 1)

        ext_control_layout = QHBoxLayout()
        self.ext_slider = QSlider(Qt.Orientation.Horizontal)
        self.ext_slider.setEnabled(False)
        self.ext_slider.valueChanged.connect(self._on_ext_slider_changed)

        self.ext_spinbox = QSpinBox()
        self.ext_spinbox.setEnabled(False)
        self.ext_spinbox.valueChanged.connect(self._on_ext_spinbox_changed)

        ext_control_layout.addWidget(self.ext_slider, 1)
        ext_control_layout.addWidget(self.ext_spinbox)
        main_layout.addLayout(ext_control_layout)

        rotation_layout = QHBoxLayout()
        rotation_layout.addWidget(QLabel("Rotation:"))
        self.rot_spinbox = QSpinBox()
        self.rot_spinbox.setRange(-180, 180)
        self.rot_spinbox.setValue(0)
        self.rot_spinbox.valueChanged.connect(self._on_rotation_changed)
        rotation_layout.addWidget(self.rot_spinbox)
        main_layout.addLayout(rotation_layout)

        self.ext_time_label = QLabel("Frame: 0 | PTS: 0")
        main_layout.addWidget(self.ext_time_label)

        self.btn_generate = QPushButton("Generate .time file")
        self.btn_generate.setEnabled(False)
        self.btn_generate.clicked.connect(self._generate_time_file)
        main_layout.addWidget(self.btn_generate)

    def _render_current_pixmap(self) -> None:
        if self._current_pixmap and not self._current_pixmap.isNull():
            angle = self.rot_spinbox.value()
            pix = self._current_pixmap
            if angle != 0:
                t = QTransform().rotate(angle)
                pix = pix.transformed(t, Qt.TransformationMode.SmoothTransformation)

            self.ext_img_label.setPixmap(
                pix.scaled(
                    self.ext_img_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_current_pixmap()

    def _on_rotation_changed(self, val: int) -> None:
        if self.ext_slider.isEnabled():
            self._update_ext_frame(self.ext_slider.value())

    def _load_external_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select External Video",
            "",
            "Video Files (*.mp4 *.mov *.avi *.mkv);;All Files (*)",
        )
        if not path:
            return

        try:
            self.ext_video_path = Path(path)
            try:
                if str(self.ext_video_path).lower().endswith((".mov", ".mp4")):
                    import functools

                    if not hasattr(plv.Reader, "_is_patched_for_mov"):
                        target = plv.Reader._container
                        if hasattr(target, "func"):
                            old_func = target.func

                            @functools.wraps(old_func)
                            def patched_func(self_reader: Any) -> Any:
                                if (
                                    str(self_reader.source)
                                    .lower()
                                    .endswith((".mov", ".mp4"))
                                ):
                                    import av

                                    container = av.open(
                                        str(self_reader.source), format=None
                                    )
                                    for stream in container.streams.video:
                                        stream.thread_type = "FRAME"
                                    return container
                                return old_func(self_reader)

                            new_cached_prop = functools.cached_property(patched_func)
                            new_cached_prop.__set_name__(plv.Reader, "_container")
                            plv.Reader._container = new_cached_prop  # type: ignore[attr-defined]
                            plv.Reader._is_patched_for_mov = True  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning(f"Monkey patch failed: {e}")

            self.ext_img_label.setText("Reading PTS... Please wait")

            self._worker = PTSExtractorWorker(str(path))
            self._thread = QThread()
            self._worker.moveToThread(self._thread)

            self._thread.started.connect(self._worker.run)
            self._worker.finished_extraction.connect(self._on_pts_extracted)
            self._worker.error.connect(self._on_pts_error)

            self._worker.finished_extraction.connect(self._thread.quit)
            self._worker.finished_extraction.connect(self._worker.deleteLater)
            self._thread.finished.connect(self._thread.deleteLater)

            self._worker.error.connect(self._thread.quit)
            self._worker.error.connect(self._worker.deleteLater)

            self._thread.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load external video:\n{e}")

    def _on_pts_error(self, err: str) -> None:
        QMessageBox.critical(
            self, "Error", f"Failed to load external video reader in background:\n{err}"
        )
        self.ext_img_label.setText("No external video")

    def _on_pts_extracted(self, reader: Any, pts: Any, tb: float, count: int) -> None:
        self.reader = reader
        self.ext_pts = pts
        self.ext_time_base = tb
        self.ext_frames_count = count
        self._finish_loading()

    def _finish_loading(self) -> None:
        try:
            if self.ext_pts is None or self.ext_time_base is None:
                logger.warning(
                    "Could not find PTS/time_base. Assuming uniform frame rate."
                )
                fps = float(getattr(self.reader, "average_rate", 30.0))
                if fps == 0:
                    fps = 30.0
                self.ext_time_base = 1.0 / fps
                self.ext_pts = list(range(self.ext_frames_count))

            self.ext_slider.setRange(0, self.ext_frames_count - 1)
            self.ext_slider.setEnabled(True)
            self.ext_slider.setValue(0)

            self.ext_spinbox.setRange(0, self.ext_frames_count - 1)
            self.ext_spinbox.setEnabled(True)
            self.ext_spinbox.setValue(0)

            self.btn_generate.setEnabled(True)
            self._update_ext_frame(0)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to finish loading video:\n{e}")

    def _update_ext_frame(self, idx: int) -> None:
        if not self.reader:
            return

        try:
            frame = self.reader[idx]
            qimg = frame_to_qimage(frame)
            if qimg:
                self._current_pixmap = QPixmap.fromImage(qimg)
                self._render_current_pixmap()

            pts_str = "N/A"
            if self.ext_pts is not None and idx < len(self.ext_pts):
                pts_str = str(self.ext_pts[idx])
            self.ext_time_label.setText(f"Frame: {idx} | PTS: {pts_str}")
        except Exception:
            logger.exception(f"Error updating Ext frame {idx}:")

    def _on_ext_slider_changed(self, val: int) -> None:
        if self.ext_spinbox.value() != val:
            self.ext_spinbox.blockSignals(True)
            self.ext_spinbox.setValue(val)
            self.ext_spinbox.blockSignals(False)
        self._update_ext_frame(val)

    def _on_ext_spinbox_changed(self, val: int) -> None:
        if self.ext_slider.value() != val:
            self.ext_slider.blockSignals(True)
            self.ext_slider.setValue(val)
            self.ext_slider.blockSignals(False)
        self._update_ext_frame(val)

    def _generate_time_file(self) -> None:
        if (
            not self.ext_video_path
            or not self.reader
            or self.ext_pts is None
            or self.ext_time_base is None
        ):
            return

        try:
            neon_ts = self.plugin.app.current_ts
        except AttributeError:
            QMessageBox.warning(
                self, "Warning", "Could not get current playback time from Neon Player."
            )
            return

        ext_idx = self.ext_slider.value()
        pts_sync = self.ext_pts[ext_idx]
        tb = float(self.ext_time_base)

        out_timestamps = []
        for i in range(self.ext_frames_count):
            if i < len(self.ext_pts):
                pts_i = self.ext_pts[i]
                diff_sec = (pts_i - pts_sync) * tb
                t_i_ns = neon_ts + int(diff_sec * 1e9)
                out_timestamps.append(t_i_ns)
            else:
                out_timestamps.append(neon_ts)

        # Auto-rename the video to start with 'recording_' if needed
        new_name = self.ext_video_path.name
        if not new_name.startswith("recording_"):
            new_name = f"recording_{new_name}"

        suffix = Path(new_name).suffix.lower()
        new_name = Path(new_name).stem + suffix

        new_path = self.ext_video_path.with_name(new_name)

        if self.ext_video_path != new_path:
            try:
                self.ext_video_path.rename(new_path)
                self.ext_video_path = new_path
            except Exception as e:
                QMessageBox.warning(
                    self, "Warning", f"Could not rename video to {new_name}:\n{e}"
                )

        out_path = self.ext_video_path.with_suffix(".time")

        try:
            np.savetxt(str(out_path), out_timestamps, fmt="%d")
            QMessageBox.information(
                self,
                "Success",
                f"Saved {len(out_timestamps)} timestamps to:\n{out_path}",
            )

            for plugin in self.plugin.app.plugins:
                if type(plugin).__name__ == "ExternalCameraPlugin":
                    plugin.on_recording_loaded(self.plugin.app.recording)
                    break

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save .time file:\n{e}")


class ExternalCameraManualSyncPlugin(Plugin):
    label = "External Camera Manual Sync"

    def __init__(self) -> None:
        super().__init__()
        self._show_sync_tool = False
        self._dock: SyncDockWidget | None = None

    @action
    @action_params(
        label="External Camera Manual Sync",
        order=101,
        compact=True,
        icon=QIcon.fromTheme("view-refresh"),
    )
    def toggle_sync_tool(self, **kwargs) -> None:
        self._show_sync_tool = not self._show_sync_tool
        if self._show_sync_tool:
            self._setup_dock_widget()
        else:
            self._remove_dock_widget()

    def _setup_dock_widget(self) -> None:
        if not hasattr(self, "app") or not getattr(self.app, "main_window", None):
            return
        if self._dock:
            return

        self._dock = SyncDockWidget(self, self.app.main_window)
        self.app.main_window.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, self._dock
        )

    def _remove_dock_widget(self) -> None:
        if self._dock:
            self.app.main_window.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None

    def on_recording_loaded(self, recording: NeonRecording) -> None:
        pass

    def extract_pts_job(self) -> Generator[ProgressUpdate, None, None]:
        path = getattr(self, "_pending_pts_path", None)

        extracted_pts = None
        extracted_tb = None
        extracted_len = 0
        reader = None

        try:
            if path:
                yield ProgressUpdate(0.1)

                import pupil_labs.video as plv

                reader = plv.Reader(path, stream="video")

                extracted_pts = reader.pts
                extracted_len = len(extracted_pts)

                yield ProgressUpdate(0.9)

                if reader._stream.time_base is not None:
                    extracted_tb = float(reader._stream.time_base)
                else:
                    extracted_tb = 1.0 / float(
                        getattr(reader, "average_rate", 30.0) or 30.0
                    )

        except Exception as e:
            logger.warning(f"Could not read exact PTS using AV: {e}")

        self._extracted_reader = reader
        self._extracted_pts = extracted_pts
        self._extracted_time_base = extracted_tb
        self._extracted_len = extracted_len

        yield ProgressUpdate(1.0)
