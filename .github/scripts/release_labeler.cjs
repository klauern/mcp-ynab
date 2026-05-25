const releaseLabels = ["patch", "minor", "major"];
const releaseRelevantPatterns = [
  /^src\//,
  /^tests\//,
  /^pyproject\.toml$/,
  /^uv\.lock$/,
  /^Taskfile\.yml$/,
];

function hasReleaseRelevantChange(files) {
  return files.some((file) => releaseRelevantPatterns.some((pattern) => pattern.test(file.filename)));
}

async function ensureLabel(github, context, name, color, description) {
  const owner = context.repo.owner;
  const repo = context.repo.repo;

  try {
    await github.rest.issues.getLabel({ owner, repo, name });
  } catch (error) {
    if (error.status !== 404) {
      throw error;
    }
    await github.rest.issues.createLabel({ owner, repo, name, color, description });
  }
}

async function listPullRequestFiles(github, context, pullNumber) {
  const files = await github.paginate(github.rest.pulls.listFiles, {
    owner: context.repo.owner,
    repo: context.repo.repo,
    pull_number: pullNumber,
    per_page: 100,
  });
  return files;
}

async function addLabel(github, context, issueNumber, label) {
  await github.rest.issues.addLabels({
    owner: context.repo.owner,
    repo: context.repo.repo,
    issue_number: issueNumber,
    labels: [label],
  });
}

async function removeLabel(github, context, issueNumber, label) {
  try {
    await github.rest.issues.removeLabel({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: issueNumber,
      name: label,
    });
  } catch (error) {
    if (error.status !== 404) {
      throw error;
    }
  }
}

module.exports = async ({ github, context, core }) => {
  const pull = context.payload.pull_request;
  if (!pull) {
    core.info("No pull request in event payload; skipping.");
    return;
  }

  await ensureLabel(github, context, "patch", "0E8A16", "Release: patch version bump");
  await ensureLabel(github, context, "minor", "1D76DB", "Release: minor version bump");
  await ensureLabel(github, context, "major", "B60205", "Release: major version bump");

  const labels = new Set(pull.labels.map((label) => label.name));
  const explicitLabels = ["minor", "major"].filter((label) => labels.has(label));
  const files = await listPullRequestFiles(github, context, pull.number);
  const releaseRelevant = hasReleaseRelevantChange(files);

  if (explicitLabels.length > 0) {
    await removeLabel(github, context, pull.number, "patch");
    core.info(`Explicit release label present (${explicitLabels.join(", ")}); removed patch.`);
    return;
  }

  if (releaseRelevant) {
    if (!labels.has("patch")) {
      await addLabel(github, context, pull.number, "patch");
      core.info("Added patch label for release-relevant changes.");
    } else {
      core.info("Patch label already present.");
    }
    return;
  }

  if (labels.has("patch")) {
    await removeLabel(github, context, pull.number, "patch");
    core.info("Removed patch label because no release-relevant files changed.");
  } else {
    core.info("No release-relevant files changed; no release label needed.");
  }
};
