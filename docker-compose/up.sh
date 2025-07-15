#!/bin/bash

# Run Docker Compose

docker compose up --detach --force-recreate --renew-anon-volumes --always-recreate-deps
