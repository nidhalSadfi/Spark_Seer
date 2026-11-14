-- Run by the postgres image only when it initialises an empty data volume (/docker-entrypoint-initdb.d).
-- Creates the database the pg-marked backend tests use (TEST_DATABASE_URL in .env.example); the
-- role running this script (POSTGRES_USER) owns it. On an existing volume, run this statement by hand.
CREATE DATABASE emberguard_test;
