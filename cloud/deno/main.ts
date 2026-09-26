/**
 * Deno Deploy cron trigger. Dispatches GitHub Actions.
 * Deno's full free limits need a card to verify the organisation, so this stays off
 * until you deploy it yourself. Without GH_DISPATCH_TOKEN the handler returns immediately.
 */
const token = (Deno.env.get("GH_DISPATCH_TOKEN") ?? "").trim();
const repo = (Deno.env.get("GH_REPO") ?? "").trim();
const ref = (Deno.env.get("GH_REF") ?? "main").trim();

Deno.cron("leadlane-collect", "17 */3 * * *", async () => {
  if (!token || !repo) {
    return;
  }
  await fetch(`https://api.github.com/repos/${repo}/actions/workflows/collect.yml/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "Leadlane-deno-trigger",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref }),
  });
});

Deno.serve(() => new Response("Leadlane Deno trigger. Cron does nothing without GH_DISPATCH_TOKEN."));
