// Sanity checks for personas.js. Run: node ai-influencers/validate.js
const PERSONAS = require("./personas.js");

const errors = [];
const fail = (p, msg) => errors.push(`${p.id || "?"}: ${msg}`);
const REQUIRED = ["id", "niche", "name", "handle", "age", "location", "bio", "tagline", "backstory",
  "personality", "voice", "visual", "audience", "pillars", "formats", "cadence", "samplePosts",
  "monetization", "brandCategories", "guardrails"];

if (PERSONAS.length !== 10) errors.push(`expected 10 personas, got ${PERSONAS.length}`);

for (const key of ["id", "niche", "name", "handle"]) {
  const vals = PERSONAS.map((p) => p[key]);
  const dupes = vals.filter((v, i) => vals.indexOf(v) !== i);
  if (dupes.length) errors.push(`duplicate ${key}: ${dupes.join(", ")}`);
}

for (const p of PERSONAS) {
  for (const k of REQUIRED) if (p[k] === undefined || p[k] === "") fail(p, `missing ${k}`);
  // Instagram handles: 1–30 chars of letters, numbers, periods, underscores.
  const h = (p.handle || "").replace(/^@/, "");
  if (!/^[A-Za-z0-9._]{1,30}$/.test(h)) fail(p, `invalid IG handle "${p.handle}"`);
  if (h.endsWith(".") || h.includes("..")) fail(p, `IG handle cannot end with or repeat periods`);
  // Instagram bio limit is 150 characters.
  if ([...p.bio].length > 150) fail(p, `bio is ${[...p.bio].length} chars (max 150)`);
  if (!/\bAI\b/.test(p.bio)) fail(p, "bio must disclose AI");
  if (!(p.age >= 18)) fail(p, "persona must be an adult");
  const fmtTotal = Object.values(p.formats || {}).reduce((a, b) => a + b, 0);
  if (fmtTotal !== 100) fail(p, `format mix sums to ${fmtTotal}%, expected 100`);
  if (!p.samplePosts || p.samplePosts.length < 3) fail(p, "needs at least 3 sample posts");
  for (const post of p.samplePosts || []) {
    if (!post.hashtags.includes("#AIgenerated")) fail(p, `post "${post.hook}" missing #AIgenerated`);
    if (post.hashtags.length > 30) fail(p, `post "${post.hook}" exceeds 30 hashtags`);
    if ((post.caption + " " + post.hashtags.join(" ")).length > 2200) fail(p, `caption too long`);
  }
  for (const c of p.visual.palette) if (!/^#[0-9A-Fa-f]{6}$/.test(c)) fail(p, `bad color ${c}`);
  if (!p.guardrails.length) fail(p, "needs guardrails");
  if (p.image && !require("fs").existsSync(require("path").join(__dirname, p.image))) fail(p, `image not found: ${p.image}`);
}

if (errors.length) {
  console.error(`✗ ${errors.length} problem(s):\n  ` + errors.join("\n  "));
  process.exit(1);
}
console.log(`✓ ${PERSONAS.length} personas valid (${PERSONAS.map((p) => p.niche).join(" · ")})`);
