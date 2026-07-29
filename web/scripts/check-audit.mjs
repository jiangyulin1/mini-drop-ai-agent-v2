import { spawnSync } from "node:child_process";

// React Router's current advisory concerns its server/RSC action pipeline.
// Mini-Drop is a client-only BrowserRouter SPA and does not import or expose
// React Router server actions. Keep this narrow exception visible and fail on
// every other high/critical production advisory.
const allowedAdvisories = new Set(["GHSA-qwww-vcr4-c8h2"]);

const npmCli = process.env.npm_execpath;
const command = npmCli ? process.execPath : "npm";
const commandArgs = npmCli
  ? [npmCli, "audit", "--omit=dev", "--json"]
  : ["audit", "--omit=dev", "--json"];
const result = spawnSync(command, commandArgs, {
  encoding: "utf8",
});

let report;
try {
  report = JSON.parse(result.stdout || "{}");
} catch {
  process.stderr.write(result.stderr || result.stdout || "npm audit failed\n");
  process.exit(1);
}

const blocked = [];
const accepted = [];
const acceptedNames = new Set();
const entries = Object.entries(report.vulnerabilities || {});
for (const [name, vulnerability] of entries) {
  if (!["high", "critical"].includes(vulnerability.severity)) continue;
  const advisories = (vulnerability.via || [])
    .filter((item) => typeof item === "object")
    .map((item) => item.url?.split("/").at(-1))
    .filter(Boolean);
  if (advisories.length > 0 && advisories.every((id) => allowedAdvisories.has(id))) {
    accepted.push(`${name}: ${advisories.join(", ")}`);
    acceptedNames.add(name);
  }
}

for (const [name, vulnerability] of entries) {
  if (!["high", "critical"].includes(vulnerability.severity) || acceptedNames.has(name)) continue;
  const advisories = (vulnerability.via || [])
    .filter((item) => typeof item === "object")
    .map((item) => item.url?.split("/").at(-1))
    .filter(Boolean);
  const transitive = (vulnerability.via || []).filter((item) => typeof item === "string");
  if (
    advisories.length === 0
    && transitive.length > 0
    && transitive.every((dependency) => acceptedNames.has(dependency))
  ) {
    accepted.push(`${name}: transitive through ${transitive.join(", ")}`);
    acceptedNames.add(name);
  } else {
    blocked.push(`${name}: ${advisories.join(", ") || vulnerability.severity}`);
  }
}

if (accepted.length) {
  console.log("Accepted client-only advisory exception:");
  accepted.forEach((item) => console.log(`- ${item}`));
}
if (blocked.length) {
  console.error("Unaccepted high/critical production advisories:");
  blocked.forEach((item) => console.error(`- ${item}`));
  process.exit(1);
}
console.log("Production dependency audit gate passed.");
