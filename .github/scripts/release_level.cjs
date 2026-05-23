const releaseLabels = ["patch", "minor", "major"];

function resolveReleaseLevel(labels) {
  if (labels.includes("major")) {
    return "major";
  }
  if (labels.includes("minor")) {
    return "minor";
  }
  if (labels.includes("patch")) {
    return "patch";
  }
  return "";
}

module.exports = async ({ context, core }) => {
  const pull = context.payload.pull_request;
  if (!pull) {
    core.setFailed("No pull request found in event payload.");
    return;
  }

  if (!pull.merged) {
    core.info("Pull request was closed without merge; skipping release.");
    core.setOutput("should_release", "false");
    return;
  }

  const labels = pull.labels.map((label) => label.name);
  const presentReleaseLabels = releaseLabels.filter((label) => labels.includes(label));
  const level = resolveReleaseLevel(presentReleaseLabels);

  if (!level) {
    core.info("Merged pull request has no patch/minor/major label; skipping release.");
    core.setOutput("should_release", "false");
    return;
  }

  if (presentReleaseLabels.length > 1) {
    core.warning(
      `Multiple release labels found (${presentReleaseLabels.join(", ")}); using ${level}.`
    );
  }

  core.setOutput("should_release", "true");
  core.setOutput("level", level);
};
