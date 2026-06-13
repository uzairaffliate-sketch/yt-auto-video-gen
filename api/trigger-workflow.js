/**
 * Vercel serverless function – bridge between frontend and GitHub Actions.
 *
 * Endpoints:
 *   POST /api/trigger-workflow   → starts a workflow run
 *   GET  /api/check-status       → polls run status & returns download link
 */

// Environment variables (set in Vercel dashboard)
const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_OWNER = process.env.GITHUB_OWNER;
const GITHUB_REPO = process.env.GITHUB_REPO;
const WORKFLOW_FILENAME = 'generate-video.yml';

// CORS headers
const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
};

function respond(statusCode, body) {
  return {
    statusCode,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

// Simple GitHub API request helper (Node 18+ fetch)
async function githubRequest(method, path, body) {
  const url = `https://api.github.com${path}`;
  const options = {
    method,
    headers: {
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'YT-Auto-Video-Gen/1.0',
    },
  };
  if (body) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GitHub API error (${res.status}): ${text}`);
  }
  return res.json();
}

// Find workflow ID by filename
async function getWorkflowId() {
  const workflows = await githubRequest('GET', `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows`);
  const wf = workflows.workflows.find(w => w.path === `.github/workflows/${WORKFLOW_FILENAME}`);
  if (!wf) throw new Error(`Workflow '${WORKFLOW_FILENAME}' not found.`);
  return wf.id;
}

// Trigger workflow dispatch
async function triggerWorkflow(workflowId, inputs) {
  await githubRequest(
    'POST',
    `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${workflowId}/dispatches`,
    { ref: 'main', inputs }
  );
  // Get the latest run (should be the one we just triggered)
  const runs = await githubRequest(
    'GET',
    `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${workflowId}/runs?per_page=1`
  );
  if (runs.workflow_runs.length === 0) throw new Error('No workflow runs found.');
  const run = runs.workflow_runs[0];
  return { run_id: run.id, run_url: run.html_url };
}

// Check run status and get artifact download URL if completed
async function checkRunStatus(runId) {
  const run = await githubRequest(
    'GET',
    `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${runId}`
  );
  const result = {
    status: run.status,
    conclusion: run.conclusion,
    download_url: null,
  };

  if (run.status === 'completed' && run.conclusion === 'success') {
    // Fetch artifacts
    const artifacts = await githubRequest(
      'GET',
      `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${runId}/artifacts`
    );
    const videoArtifact = artifacts.artifacts.find(a => a.name === 'generated-video');
    if (videoArtifact) {
      result.download_url = `${process.env.VERCEL_URL}/api/download-artifact?artifact_id=${videoArtifact.id}`;
    }
  }
  return result;
}

// Proxy endpoint to download artifact (zip containing the mp4)
async function downloadArtifact(artifactId) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/artifacts/${artifactId}/zip`;
  const headers = {
    Authorization: `Bearer ${GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'User-Agent': 'YT-Auto-Video-Gen/1.0',
  };
  const response = await fetch(url, { headers });
  if (!response.ok) {
    throw new Error('Failed to download artifact');
  }
  return new Response(response.body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('content-type') || 'application/zip',
      'Content-Disposition': 'attachment; filename="video.zip"',
      ...CORS_HEADERS,
    },
  });
}

// Main Vercel serverless handler
export default async function handler(req, res) {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  const url = new URL(req.url);
  const path = url.pathname.replace(/\/api\/?/, ''); // relative path without /api/

  try {
    if (req.method === 'POST' && path === 'trigger-workflow') {
      const body = await req.json();
      const { script, aspect, quality, voiceover_type, audio_url } = body;

      if (!script || !aspect || !quality || !voiceover_type) {
        return respond(400, { error: 'Missing required fields.' });
      }

      // ✅ Allowed voiceover types
      const ALLOWED_TYPES = ['tts', 'custom_audio', 'no_audio'];
      if (!ALLOWED_TYPES.includes(voiceover_type)) {
        return respond(400, { error: `Invalid voiceover_type. Allowed: ${ALLOWED_TYPES.join(', ')}` });
      }

      const inputs = {
        script,
        aspect_ratio: aspect,
        quality,
        voiceover_type,
        audio_url: audio_url || '',
      };

      const workflowId = await getWorkflowId();
      const run = await triggerWorkflow(workflowId, inputs);
      return respond(200, run);

    } else if (req.method === 'GET' && path === 'check-status') {
      const runId = url.searchParams.get('run_id');
      if (!runId) return respond(400, { error: 'Missing run_id parameter.' });
      const result = await checkRunStatus(Number(runId));
      return respond(200, result);

    } else if (req.method === 'GET' && path === 'download-artifact') {
      const artifactId = url.searchParams.get('artifact_id');
      if (!artifactId) return respond(400, { error: 'Missing artifact_id.' });
      return await downloadArtifact(Number(artifactId));

    } else {
      return respond(404, { error: 'Not found' });
    }
  } catch (error) {
    console.error(error);
    return respond(500, { error: error.message });
  }
}