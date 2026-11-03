# Docker docs snapshot

27 pages of the Docker and Compose documentation that knowledge search ingests on first start: restart policies, memory limits and the OOM killer, exit codes, health checks, logging, the daemon's logs, live restore, networking and published ports, volumes, bind mounts and tmpfs, pruning, Compose startup order, networking, environment variables and the `services` and `deploy` reference, `docker events`, `docker logs` and `docker system df`.

They come from [docker/docs](https://github.com/docker/docs), which is licensed under the Apache License 2.0; a copy is in [LICENSE](LICENSE). Each page's front matter names its published URL (`source_url`), its path in that repository (`source_path`), the commit it was read at, the day it was fetched and the licence.

The pages are changed from the originals by `scripts/fetch_docker_docs.py`: Hugo shortcodes are resolved (includes) or dropped, links keep only their text, images are dropped, the Dockerfile, Compose `services` and `docker run` references keep only the sections listed in the script, and the three CLI reference pages are built from their YAML data. Refresh them with:

```bash
uv run --project backend python scripts/fetch_docker_docs.py
cd backend && uv run python -m evals.knowledge   # then update the dated knowledge report
```
