/**
 * Cloudflare Workers Cron Trigger.
 * Dispatches the Leadlane GitHub Actions workflow. Does not collect by itself.
 * Does nothing until GH_DISPATCH_TOKEN and GH_REPO are set.
 * Leave the cron in wrangler.toml commented out until you want it on.
 */
export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  async fetch() {
    return new Response("Leadlane trigger is off until a cron calls it.", { status: 200 });
  },
};

async function dispatch(env) {
  const token = (env.GH_DISPATCH_TOKEN || "").trim();
  const repo = (env.GH_REPO || "").trim();
  const ref = (env.GH_REF || "main").trim();
  if (!token || !repo) {
    return;
  }
  const trade = (env.TRADE_PRESET || "all").trim() || "all";
  await fetch(`https://api.github.com/repos/${repo}/actions/workflows/collect.yml/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "Leadlane-cloudflare-trigger",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref, inputs: { trade_preset: trade } }),
  });
}
