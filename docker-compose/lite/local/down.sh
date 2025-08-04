#!/bin/bash

# Stop and remove all containers defined in the Docker Compose file,
# along with any anonymous volumes attached to them.
docker compose down --volumes