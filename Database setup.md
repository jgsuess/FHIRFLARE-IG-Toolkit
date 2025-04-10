we need to create the actual database file and the user table inside it. We use Flask-Migrate for this. These commands need to be run inside the running container.

Find your container ID (if you don't know it):

Bash

docker ps
(Copy the CONTAINER ID or Name for webapp-base).

Initialize the Migration Repository (ONLY RUN ONCE EVER): This creates a migrations folder in your project (inside the container, and locally if you map volumes later, but the command runs inside).

Bash

docker exec <CONTAINER_ID_or_NAME> flask db init
(Replace <CONTAINER_ID_or_NAME>)

Create the First Migration Script: Flask-Migrate compares your models (app/models.py) to the (non-existent) database and generates a script to create the necessary tables.

Bash

docker exec <CONTAINER_ID_or_NAME> flask db migrate -m "Initial migration; create user table."
(You can change the message after -m). This creates a script file inside the migrations/versions/ directory.

Apply the Migration to the Database: This runs the script generated in the previous step, actually creating the app.db file (in /app/instance/) and the user table inside it.

Bash

docker exec <CONTAINER_ID_or_NAME> flask db upgrade
After running these docker exec flask db ... commands, you should have a migrations folder in your project root locally (because it was created by code running inside the container using the project files mounted/copied) and an app.db file inside the /app/instance/ directory within the container.

Your database is now set up with a user table!


flask db init
flask db migrate -m "Initial migration; create user table."
flask db upgrade

flask db migrate -m "Add role column to user table"
flask db upgrade

flask db migrate -m "Add ModuleRegistry table"
flask db upgrade

flask db migrate -m "Add ProcessedIg table"
flask db upgrade