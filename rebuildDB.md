tep 2: Stop Container(s)

Bash

docker stop <your_container_id_or_name>
# or if using docker compose:
# docker compose down
Step 3: Delete Local Database and Migrations

On your local Windows machine:

Delete the database file: C:\GIT\SudoPas_demo\instance\app.db
Delete the test database file (if it exists): C:\GIT\SudoPas_demo\instance\test.db
Delete the entire migrations folder: C:\GIT\SudoPas_demo\migrations\
Step 4: Start a Temporary Container

Bash

docker compose up -d
# OR if not using compose:
# docker run ... your-image-name ...
(Get the new container ID/name)

Step 5: Initialize Migrations Inside Container

Bash

docker exec -w /app <temp_container_id_or_name> flask db init
Step 6: Copy New migrations Folder Out to Local

Run this in your local PowerShell or Command Prompt:

PowerShell

docker cp <temp_container_id_or_name>:/app/migrations C:\GIT\SudoPas_demo\migrations
Verify the migrations folder now exists locally again, containing alembic.ini, env.py, etc., but the versions subfolder should be empty.

Step 7: Stop Temporary Container

Bash

docker stop <temp_container_id_or_name>
# or if using docker compose:
# docker compose down
Step 8: Rebuild Docker Image

Crucially, rebuild the image to include the latest models.py and the new empty migrations folder:

Bash

docker compose build
# OR
# docker build -t your-image-name .
Step 9: Start Final Container

Bash

docker compose up -d
# OR
# docker run ... your-image-name ...
(Get the final container ID/name)

Step 10: Create Initial Migration Script

Now, generate the first migration script based on your current models:

Bash

docker exec -w /app <final_container_id_or_name> flask db migrate -m "Initial migration with User, ModuleRegistry, ProcessedIg"
Check that a new script appeared in your local migrations/versions/ folder.

Step 11: Apply Migration (Create DB & Tables)

Run upgrade to create the new app.db and apply the initial schema:

Bash

docker exec -w /app <final_container_id_or_name> flask db upgrade
After this, you should have a completely fresh database matching your latest models. You will need to:

Create your admin user again via signup or a dedicated command (if you create one).
Re-import any FHIR IGs via the UI if you want data to test with.