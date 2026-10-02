# Map images

The site's map banners can show in-game screenshots from https://github.com/neustcs/cs2mapsthumbnails
(commit of 2024-04-25). That repository states no licence and the game content belongs to Valve, so the
pictures are not part of this repository. On the server, `deploy/server-deploy.sh` runs
`cs2-analyzer map-images`, which downloads them into `data/maps/images/` (resized to 960x540), and the API
serves them at `/maps/<map>.jpg`. Without a picture the site shows its drawn banner. Train has no image there.

Locally you can run the same command, or drop your own `<map>.jpg` into this folder.

| Map | Source file |
| --- | --- |
| de_mirage | de_mirage_a_site.jpg |
| de_dust2 | de_dust2_a_long.jpg |
| de_inferno | de_inferno_a_site.jpg |
| de_nuke | de_nuke_outside.jpg |
| de_ancient | de_ancient_site_a.jpg |
| de_anubis | de_anubis_a_site.jpg |
| de_vertigo | de_vertigo_a_site.jpg |
| de_overpass | de_overpass_a_site.jpg |
