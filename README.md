# External Camera Manual Sync Plugin

External camera manual sync plugin. Loads an external camera video (.mp4) and presents a slider to manually sync frames.

Designed for external cameras like GoPros that have been recorded without syncing. Use the slider to match the external frame with the scene camera. Once matched, press "Generate .time" to create a compatible synced format that can be seamlessly used with the `external_camera.py` plugin.

## Installation
For instructions on how to install and manage Neon Player plugins, please refer to the [Neon Player Plugin Documentation](https://docs.pupil-labs.com/neon/neon-player/plugin-api/#adding-a-plugin).

## Usage
1. Use the **Load Video** action to load your unsynced `.mp4` external camera recording.
2. The plugin will open a side-by-side view showing both the Neon scene camera and the external camera.
3. Find a clear visual event (e.g., a clap or a specific sudden movement) visible in both cameras.
4. Use the slider or the frame spinbox to shift the external camera frames until they perfectly match the current scene camera frame.
5. Click the **Generate .time** action to automatically create the compatible `.time` file.
6. You can now use the resulting video and `.time` file with the standard `external_camera.py` plugin.
