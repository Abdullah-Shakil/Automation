/**
 * Vercel Hobby cron target. Hobby cron is once a day and needs no card.
 * This file does nothing unless it is deployed and GH_DISPATCH_TOKEN is set.
 * There is no vercel.json at the repo root, so a normal deploy does not schedule it.
 */
export default async function handler(req, res) {
  const token = (process.env.GH_DISPATCH_TOKEN || "").trim();
  const repo = (process.env.GH_REPO || "").trim();
  const ref = (process.env.GH_REF || "main").trim();
  const cronSecret = (process.env.CRON_SECRET || "").trim();
  if (cronSecret && req.headers.authorization !== `Bearer ${cronSecret}`) {
    res.status(401).json({ ok: false });
    return;
  }
  if (!token || !repo) {
    res.status(200).json({ ok: false, detail: "Off until GH_DISPATCH_TOKEN and GH_REPO are set." });
    return;
  }
  const trade = (process.env.TRADE_PRESET || "all").trim() || "all";
  const response = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/collect.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "User-Agent": "Leadlane-vercel-trigger",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref, inputs: { trade_preset: trade } }),
    },
  );
  res.status(200).json({ ok: response.ok, status: response.status });
}
