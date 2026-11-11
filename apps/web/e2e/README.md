# Workflow browser tests

These Playwright tests run Chromium against the real Web application and Django API. They use a disposable project and session, not mocked workflow responses.

1. Start isolated PostgreSQL, Redis/Valkey and RabbitMQ. Set `DATABASE_URL`, `REDIS_URL`, `AMQP_URL`, `DJANGO_SETTINGS_MODULE=plane.settings.test` and a fixed **test-only** `SECRET_KEY`. Apply migrations.
2. From `apps/api`, run the seed with that same environment:

   ```bash
   WORKFLOW_E2E_ALLOW_SEED=1 .venv/bin/python ../web/e2e/seed.py
   ```

   It writes a temporary session fixture to `/tmp/plane-workflow-browser-fixture.json`. Never use a production database or commit that file, traces or cookies. Each seed creates an isolated fake workspace and project.

3. Start the API with the **same** `SECRET_KEY` used by the seed:

   ```bash
   WEB_URL=http://127.0.0.1:3107 APP_BASE_URL=http://127.0.0.1:3107 \
     .venv/bin/python manage.py runserver 127.0.0.1:8127 --noreload
   ```

4. From the repository root, start Web and run tests:

   ```bash
   VITE_API_BASE_URL=http://127.0.0.1:8127 VITE_WEB_BASE_URL=http://127.0.0.1:3107 \
     pnpm --filter web exec react-router dev --port 3107
   pnpm --filter web test:e2e
   ```

`WORKFLOW_E2E_API_URL`, `WORKFLOW_E2E_WEB_URL`, `WORKFLOW_E2E_FIXTURE` and `WORKFLOW_E2E_CHROMIUM` override defaults. Chromium defaults to `/usr/bin/chromium`; otherwise install a matching browser with Playwright. The API must expose `X-Plane-Workflow` through CORS when origins differ. Tests fix browser time to 2026-11-10 for repeatable date selection, without pausing timers.

The suite covers persisted configuration and stale-version handling, child completion/reopening, field collection, empty requirement confirmation, server permission changes and optimistic rollback, and real kanban drag/drop. It also covers missing task-path messages followed by adding a working path, hidden creation affordances in all five layouts (including kanban swimlanes and empty states), and creating Intake submissions through the real modal with a configured non-Triage state and required fields. API contract tests separately cover Space submissions and backend permissions; this suite does not run a second Space frontend.
