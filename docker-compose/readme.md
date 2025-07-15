# FHIRFLARE IG Toolkit

This directory provides scripts and configuration to start and stop a FHIRFLARE instance with an attached HAPI FHIR server using Docker Compose.

## Usage

- To start the FHIRFLARE toolkit and HAPI server:
  ```sh
  ./docker-compose/up.sh
  ```

- To stop and remove the containers and volumes:
  ```sh
  ./docker-compose/down.sh
  ```

The web interface will be available at [http://localhost:5000](http://localhost:5000) and the HAPI FHIR server at [http://localhost:8080/fhir](http://localhost:8080/fhir).

For more details, see the configuration files in this directory.