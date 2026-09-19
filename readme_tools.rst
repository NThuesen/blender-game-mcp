#####
Tools
#####

The tools exposed by the MCP server.

..
   Generated from the tool doc-strings by ``make readme_update``.
   Do not edit the listing between the sentinels below by hand.

.. BEGIN TOOL LISTING

\
``configure_game_scene``
   Configure the active Blender scene for 2D game-asset rendering.

``execute_blender_code``
   Execute Python code in the connected Blender instance.

``execute_blender_code_for_cli``
   Execute Python code in a background Blender process.

``get_game_frame_as_image``
   Render a requested animation frame and return it as a PNG image for
   visual QA while restoring the current frame and temporary render settings.

``get_blendfile_summary_datablocks``
   Return a summary of the blend file: data-block counts, active workspace,
   and render engine.

``get_blendfile_summary_datablocks_for_cli``
   Return a data-block summary by opening *blend_file* in background
   Blender.

``get_blendfile_summary_missing_files``
   Report external file references that are missing from disk (images,
   libraries, fonts, sounds, movie clips, caches, sequences).

``get_blendfile_summary_missing_files_for_cli``
   Report missing file references by opening *blend_file* in background
   Blender.

``get_blendfile_summary_of_linked_libraries``
   Return a tree of directly and indirectly linked library files.

``get_blendfile_summary_of_linked_libraries_for_cli``
   Return linked-library info by opening *blend_file* in background
   Blender.

``get_blendfile_summary_path_info``
   Simple/fast access to the blend file's path, save status, age, and
   backups.

``get_blendfile_summary_path_info_for_cli``
   Return path info by opening *blend_file* in background Blender.

``get_blendfile_summary_usage_guess``
   Guess the primary use-cases of the current blend file (scored 0-100 with
   certainty).

``get_blendfile_summary_usage_guess_for_cli``
   Guess use-cases by opening *blend_file* in background Blender.

``get_object_detail_summary``
   Return a structured summary of the object identified by *name*.

``get_objects_summary``
   Return the scene's collection hierarchy and their objects.

``get_python_api_docs``
   Return the Blender Python API docs for *identifier*, or list modules
   matching a trailing-``*`` discovery pattern.

``get_runtime_python_api_docs``
   Return compact docs for an exact Python API *identifier* from the
   connected Blender runtime.

``get_runtime_python_api_docs_for_cli``
   Return compact docs for an exact Python API *identifier* from a
   background runtime.

``get_screenshot_of_area_as_image``
   Take a screenshot of a single Blender area and return it as a PNG image.

``get_screenshot_of_window_as_image``
   Take a screenshot of the entire Blender window and return it as a PNG
   image.

``get_screenshot_of_window_as_json``
   Return a JSON description of the Blender window layout, areas, active
   object, and selection.

``inspect_game_scene``
   Return a compact readiness report for PNG/RGBA game-asset rendering.

``inspect_game_transform_frames``
   Sample one object's evaluated transform at selected animation frames and
   restore the current frame afterwards.

``jump_to_tab_by_name``
   Switch the active workspace tab to *name*.

``jump_to_tab_by_space_type``
   Switch to a workspace whose main area matches *space_type*.

``jump_to_view3d_object_by_name``
   Move the 3D viewport to focus on an object by *name*.

``jump_to_view3d_object_data_by_name``
   Move the 3D viewport to the object whose data block matches *name*.

``render_thumbnail_to_path``
   Render a small, low-quality thumbnail to *output_path* (temporarily
   overrides settings).

``render_viewport_to_path``
   Render the current scene to *output_path* using current render settings.

``set_game_transform_keyframes``
   Insert bounded batches of location, Euler rotation or scale keyframes for
   game animation.

``search_api_docs``
   Full-text search over the bundled Blender Python API reference.

``search_manual_docs``
   Full-text search over the bundled Blender user manual.

.. END TOOL LISTING
