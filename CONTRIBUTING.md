# Contributing to django-invoice-reminders

Thanks for taking the time to contribute. Bug reports, ideas, documentation fixes and code are all welcome. This guide takes you from cloning the repo to an opened pull request.

## Ways to help

- Report a bug or suggest a feature by opening an issue. Say what you expected, what happened, and the steps to reproduce it.
- Improve the README or the docs.
- Pick up an open issue. Issues labelled `good first issue` are a good place to start.

## Picking up an issue

1. Browse the open issues and choose one.
2. Comment on it to say you'd like to work on it, and wait for a maintainer to reply before you start.
3. Work on one issue at a time.
4. If you can't finish, say so in the issue so someone else can take it.

If you want to work on something that has no issue yet, open one first and describe your idea, so you don't spend time on a change that doesn't fit.

## Set up

You need Python 3.12.

```bash
git clone https://github.com/<your-username>/django-invoice-reminders.git && cd django-invoice-reminders
python3.12 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
python manage.py migrate
```

To send real emails you need your Mailtrap settings in `.env`. **The tests need no `.env`, credentials or network**: `config/test_settings.py` sets dummy values and swaps in a fake mailer.

## Run it

```bash
python manage.py seed_demo --email you@example.com      # demo invoices at different reminder stages
python manage.py send_payment_reminders --dry-run       # shows what would be sent, sends nothing
python manage.py runserver
```

Open http://localhost:8000 for the dashboard. To use the admin at `/admin/`, run `python manage.py createsuperuser`.

## Tests and checks

```bash
pytest
DJANGO_SETTINGS_MODULE=config.test_settings python manage.py makemigrations --check --dry-run
ruff check .                    # lint, including the security rules
pip-audit -r requirements.txt   # known vulnerabilities in dependencies
```

If you change a model, include the migration. The migrations check above fails when one is missing.

Please add or update tests for the behavior you change, and run all the checks above before you open a pull request.

## Making your change

1. Fork the repository and create a branch from `main`. Use a short name that says what it does, such as `fix/expired-code-message`, `feature/dark-theme` or `docs/readme-typo`.
2. Keep each pull request focused on one change. Don't mix refactoring or formatting changes into it.
3. Write commit messages as a short, imperative summary of what changed (for example `Fix wrong time in reminder email`), and add a few lines of explanation if the reason isn't obvious.
4. Push your branch and open a pull request against `main`.

A good pull request description says:

- what the change does and why it is needed;
- how you tested it;
- which issue it closes, written as `Closes #123`;
- screenshots for any change to the user interface.

## Project layout

- `invoices/models.py`: clients, invoices and the reminder log.
- `invoices/services/`: reminders, invoicing, PDFs, email and webhooks.
- `invoices/admin.py` and `invoices/ui.py`: the admin and the dashboard.
- `templates/`: the dashboard pages and the email templates.
- `config/`: Django settings and URLs.
- `tests/`: the test suite.

## Keep secrets out

Never put API tokens, `.env` contents or real customer data in an issue, a pull request or a commit. `.env` is git-ignored, and the example file only contains placeholders.

## License

By contributing, you agree that your contributions are licensed under the project's [MIT license](LICENSE).
