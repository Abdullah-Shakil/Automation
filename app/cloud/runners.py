"""Free ways to start collection with the PC off.

Every option stays off until its switch is set. Options that need a card cannot be turned on.
The Python collector is always `python -m app.worker` on GitHub Actions.
The other free platforms only send workflow_dispatch. They do not run this app.
"""

from dataclasses import dataclass

from app.config import Settings
from app.models import AppSetting


@dataclass(frozen=True)
class CloudRunner:
    key: str
    label: str
    needs_card: bool
    env_attr: str
    summary: str
    setup: str


RUNNERS: tuple[CloudRunner, ...] = (
    CloudRunner(
        key="github_schedule",
        label="GitHub Actions schedule",
        needs_card=False,
        env_attr="leadlane_github_schedule",
        summary=(
            "No card. Public repositories get unlimited minutes on standard runners. "
            "Private repositories get 2,000 minutes a month. "
            "Scheduled runs can be delayed or skipped, so pair this with cron-job.org if you need a reliable start."
        ),
        setup=(
            "Repository variable ENABLE_SCHEDULE=true turns the 3-hour schedule on. "
            "Until that variable exists, scheduled runs are skipped. workflow_dispatch still works."
        ),
    ),
    CloudRunner(
        key="cronjob_org",
        label="cron-job.org",
        needs_card=False,
        env_attr="leadlane_cronjob_org",
        summary=(
            "No card. Free HTTPS cron, down to once a minute. "
            "It POSTs GitHub workflow_dispatch so a run is not left to the Actions scheduler."
        ),
        setup=(
            "Create a fine-grained GitHub token (Actions: Read and write, this repo only) and store it in the cron-job.org job, not in the app. "
            "Set LEADLANE_CRONJOB_ORG=true here after the job exists. Keep the interval at 3 hours on a private repo."
        ),
    ),
    CloudRunner(
        key="cloudflare",
        label="Cloudflare Workers Cron Trigger",
        needs_card=False,
        env_attr="leadlane_cloudflare_cron",
        summary=(
            "No card. Workers Free includes Cron Triggers (5 per account) and 100,000 requests a day. "
            "The worker only dispatches GitHub Actions. It cannot run the Python collector. Cron CPU on the free plan is 10 ms, which is enough for one POST."
        ),
        setup=(
            "Deploy cloud/cloudflare only after you add GH_DISPATCH_TOKEN and GH_REPO. "
            "The sample cron is commented out, so a deploy does nothing until you uncomment it. Then set LEADLANE_CLOUDFLARE_CRON=true."
        ),
    ),
    CloudRunner(
        key="deno",
        label="Deno Deploy cron",
        needs_card=True,
        env_attr="",
        summary=(
            "Deno Deploy has a free plan and Deno.cron, but full free limits stay locked until the organisation is verified with a card. "
            "Left off. The sample in cloud/deno only dispatches GitHub Actions and does nothing without a token."
        ),
        setup="Do not deploy this until you accept the card check. It is not enabled from the dashboard.",
    ),
    CloudRunner(
        key="vercel",
        label="Vercel cron (Hobby)",
        needs_card=False,
        env_attr="leadlane_vercel_cron",
        summary=(
            "No card on the Hobby plan. Hobby cron runs at most once a day, any time inside that hour, so it is a weak 24/7 trigger. "
            "Pro cron (once a minute) needs a card. The function only dispatches GitHub Actions."
        ),
        setup=(
            "There is no root vercel.json, so deploying the repo does not start a cron. "
            "Copy cloud/vercel/vercel.json.example only if a daily trigger is enough, add GH_DISPATCH_TOKEN, then set LEADLANE_VERCEL_CRON=true."
        ),
    ),
    CloudRunner(
        key="koyeb",
        label="Koyeb",
        needs_card=True,
        env_attr="",
        summary=(
            "Koyeb asks for a card to prevent abuse. The free instance is one web service, cannot be a worker, and scales to zero after an hour without traffic. Left off."
        ),
        setup="Do not deploy a collector here.",
    ),
    CloudRunner(
        key="oracle",
        label="Oracle Cloud Always Free VM",
        needs_card=True,
        env_attr="",
        summary=(
            "Always Free Ampere or AMD capacity exists, but signing up asks for a card to verify the account, and capacity is often unavailable. Left off."
        ),
        setup="Do not use this as the collector. GitHub Actions is the worker.",
    ),
)


def runner_is_on(settings: Settings, runner: CloudRunner) -> bool:
    if runner.needs_card or not runner.env_attr:
        return False
    value = str(getattr(settings, runner.env_attr, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def runner_views(settings: Settings, selected: str) -> list[dict]:
    rows = []
    for runner in RUNNERS:
        on = runner_is_on(settings, runner)
        rows.append(
            {
                "key": runner.key,
                "label": runner.label,
                "needs_card": runner.needs_card,
                "on": on,
                "selected": selected == runner.key and on,
                "summary": runner.summary,
                "setup": runner.setup,
                "card_label": "Needs a card" if runner.needs_card else "No card",
                "status": "On" if on else "Off",
            }
        )
    return rows


def selected_runner(db) -> str:
    row = db.get(AppSetting, "cloud_runner")
    return (row.value if row else "") or ""


def set_selected_runner(db, key: str) -> None:
    row = db.get(AppSetting, "cloud_runner")
    if row is None:
        db.add(AppSetting(key="cloud_runner", value=key))
    else:
        row.value = key


def get_trade_preset(db) -> str:
    """'' = all five trades; otherwise one of plumber/electrician/painter/gardener/solicitor."""
    from app.trades import normalize_preset

    row = db.get(AppSetting, "trade_preset")
    return normalize_preset(row.value if row else "")


def set_trade_preset(db, preset: str) -> str:
    from app.trades import normalize_preset

    value = normalize_preset(preset)
    row = db.get(AppSetting, "trade_preset")
    if row is None:
        db.add(AppSetting(key="trade_preset", value=value))
    else:
        row.value = value
    return value
