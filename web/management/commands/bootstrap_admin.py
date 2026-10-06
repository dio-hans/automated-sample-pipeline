import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q


class Command(BaseCommand):
    help = "Creates the initial Nonda system administrator."

    def handle(self, *args, **options):
        User = get_user_model()

        # ---------------------------------------------------------
        # 1. Do nothing if an admin already exists.
        # ---------------------------------------------------------
        existing_admin = User.objects.filter(
            Q(role=User.Role.ADMIN) | Q(is_superuser=True),
            is_active=True,
        ).first()

        if existing_admin:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Bootstrap admin already exists "
                    f"({existing_admin.username}). Skipping."
                )
            )
            return

        # ---------------------------------------------------------
        # 2. Read bootstrap credentials from environment variables.
        # ---------------------------------------------------------
        username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip()
        email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip()
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")

        missing = []

        if not username:
            missing.append("BOOTSTRAP_ADMIN_USERNAME")

        if not password:
            missing.append("BOOTSTRAP_ADMIN_PASSWORD")

        if missing:
            raise CommandError(
                "No Nonda admin exists yet, but the following "
                "bootstrap environment variables are missing: "
                + ", ".join(missing)
            )

        # ---------------------------------------------------------
        # 3. Never overwrite an existing username.
        # ---------------------------------------------------------
        if User.objects.filter(username=username).exists():
            raise CommandError(
                f"Username '{username}' already exists, "
                "but is not configured as an admin. "
                "Refusing to modify the existing account."
            )

        # ---------------------------------------------------------
        # 4. Validate the bootstrap password using Django's
        #    configured password validators.
        # ---------------------------------------------------------
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(
                "Bootstrap admin password rejected: "
                + " ".join(exc.messages)
            )

        # ---------------------------------------------------------
        # 5. Create the first Nonda administrator.
        # ---------------------------------------------------------
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Initial Nonda admin '{user.username}' created successfully."
            )
        )