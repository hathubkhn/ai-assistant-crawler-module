"""Seed django-celery-beat PeriodicTask rows (DatabaseScheduler ignores CELERY_BEAT_SCHEDULE)."""
from django.conf import settings
from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask


def _cron_field(values) -> str:
    if values == {"*"} or values == set("*"):
        return "*"
    return ",".join(str(v) for v in sorted(values))


def _crontab_schedule(cron) -> CrontabSchedule:
    return CrontabSchedule.objects.get_or_create(
        minute=_cron_field(cron.minute),
        hour=_cron_field(cron.hour),
        day_of_week=_cron_field(cron.day_of_week),
        day_of_month=_cron_field(cron.day_of_month),
        month_of_year=_cron_field(cron.month_of_year),
        timezone=settings.CELERY_TIMEZONE,
    )[0]


class Command(BaseCommand):
    help = "Create PeriodicTask entries from settings.CELERY_BEAT_SCHEDULE."

    def add_arguments(self, parser):
        parser.add_argument(
            "--test-interval-minutes",
            type=int,
            default=None,
            help="Add crawl_arxiv_new_papers every N minutes (local testing only).",
        )

    def handle(self, *args, **options):
        from celery.schedules import crontab

        created = 0
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            schedule = entry["schedule"]
            if not isinstance(schedule, crontab):
                self.stdout.write(self.style.WARNING(f"Skip {name}: unsupported schedule type"))
                continue
            cron = _crontab_schedule(schedule)
            _, was_created = PeriodicTask.objects.update_or_create(
                name=name,
                defaults={
                    "task": entry["task"],
                    "crontab": cron,
                    "enabled": True,
                },
            )
            created += 1
            self.stdout.write(f"{'Created' if was_created else 'Updated'} periodic task: {name}")

        test_mins = options["test_interval_minutes"]
        if test_mins and test_mins > 0:
            interval, _ = IntervalSchedule.objects.get_or_create(
                every=test_mins,
                period=IntervalSchedule.MINUTES,
            )
            _, was_created = PeriodicTask.objects.update_or_create(
                name="crawl-arxiv-local-test",
                defaults={
                    "task": "crawler.tasks.crawl_arxiv_new_papers",
                    "interval": interval,
                    "enabled": True,
                },
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if was_created else 'Updated'} local test task "
                    f"(every {test_mins} min)"
                )
            )

        self.stdout.write(self.style.SUCCESS(f"Done. {created} crontab task(s) synced."))
