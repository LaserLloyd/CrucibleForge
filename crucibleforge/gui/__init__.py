"""CrucibleForge web GUI — a small stdlib HTTP server + vanilla JS front end.

No CDN, no build step, no extra dependencies. `crucibleforge gui` binds
127.0.0.1:8777 by default; binding any other host requires an access token
(auto-generated and printed if not given). A token from --token,
$CRUCIBLEFORGE_GUI_TOKEN or <data dir>/gui_token also applies on loopback."""
