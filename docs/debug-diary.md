# Debug diary

One entry per problem: what you saw, why it happened, how you fixed it. These become
interview stories ("tell me about a bug you fixed").

## 12 Oct: `no configuration file provided: not found`

- **Symptom:** `docker compose up -d` printed this and started nothing.
- **Cause:** there was no `docker-compose.yml` in the folder I ran it from.
- **Fix:** created the file in `~/agentforge`. Check first with `pwd` and `ls`.

## 12 Oct: `failed to connect to the docker API ... docker.sock`

- **Symptom:** `docker compose up -d` couldn't reach Docker.
- **Cause:** Docker Desktop was closed. The `docker` command is only a client; the daemon
  that does the work runs inside Docker Desktop, reached through the `docker.sock` file.
- **Fix:** opened Docker Desktop and ran the command again.

<!-- Copy this for the next one:

## DD Mon: <the error, word for word>

- **Symptom:**
- **Cause:**
- **Fix:**
-->
