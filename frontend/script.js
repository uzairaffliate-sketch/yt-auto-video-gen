/**
 * YT Auto Video Gen – Frontend Logic
 * Handles UI interactions, API calls to Vercel serverless function,
 * and poll-based progress tracking.
 */

(function () {
  'use strict';

  // ---------- DOM elements ----------
  const scriptInput = document.getElementById('script');
  const aspectSelect = document.getElementById('aspect');
  const qualitySelect = document.getElementById('quality');
  const voiceoverRadios = document.getElementsByName('voiceover');
  const customAudioSection = document.getElementById('custom-audio-section');
  const audioUrlInput = document.getElementById('audioUrl');
  const generateBtn = document.getElementById('generateBtn');
  const btnText = document.getElementById('btnText');
  const btnSpinner = document.getElementById('btnSpinner');
  const statusArea = document.getElementById('statusArea');
  const statusIcon = document.getElementById('statusIcon');
  const statusMessage = document.getElementById('statusMessage');
  const statusLink = document.getElementById('statusLink');
  const downloadSection = document.getElementById('downloadSection');
  const downloadLink = document.getElementById('downloadLink');

  // ---------- Configuration ----------
  // Replace with your deployed Vercel function URL
  const API_BASE = 'https://your-project.vercel.app/api';

  // ---------- Helper: get selected voiceover type ----------
  function getVoiceoverType() {
    for (const radio of voiceoverRadios) {
      if (radio.checked) return radio.value;
    }
    return 'tts';
  }

  // ---------- Toggle custom audio input ----------
  function updateAudioSection() {
    const type = getVoiceoverType();
    if (type === 'custom_audio') {
      customAudioSection.classList.remove('hidden');
    } else {
      customAudioSection.classList.add('hidden');
    }
  }

  // Attach listeners to radio buttons
  for (const radio of voiceoverRadios) {
    radio.addEventListener('change', updateAudioSection);
  }
  // Initial check
  updateAudioSection();

  // ---------- Form submission ----------
  generateBtn.addEventListener('click', async () => {
    // Validate script
    const script = scriptInput.value.trim();
    if (!script) {
      alert('Please paste your video script.');
      return;
    }

    const voiceoverType = getVoiceoverType();
    let audioUrl = '';
    if (voiceoverType === 'custom_audio') {
      audioUrl = audioUrlInput.value.trim();
      if (!audioUrl) {
        alert('Please provide a direct URL to your audio file.');
        return;
      }
    }

    // Disable button, show spinner
    generateBtn.disabled = true;
    btnText.classList.add('hidden');
    btnSpinner.classList.remove('hidden');

    // Hide previous status/download
    statusArea.classList.add('hidden');
    downloadSection.classList.add('hidden');

    // Payload
    const payload = {
      script,
      aspect: aspectSelect.value,
      quality: qualitySelect.value,
      voiceover_type: voiceoverType,
      audio_url: audioUrl || undefined,
    };

    try {
      // Step 1: Trigger the workflow via Vercel function
      const triggerResp = await fetch(`${API_BASE}/trigger-workflow`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!triggerResp.ok) {
        const errText = await triggerResp.text();
        throw new Error(`Trigger failed: ${errText}`);
      }

      const triggerData = await triggerResp.json();
      const { run_id, run_url } = triggerData;

      if (!run_id) {
        throw new Error('No run_id returned from API.');
      }

      // Show status area
      statusArea.classList.remove('hidden');
      statusIcon.textContent = '⏳';
      statusMessage.textContent = 'Video generation started...';
      statusLink.href = run_url;
      statusLink.classList.remove('hidden');
      downloadSection.classList.add('hidden');

      // Step 2: Poll for completion
      await pollForCompletion(run_id);

    } catch (error) {
      console.error(error);
      statusArea.classList.remove('hidden');
      statusIcon.textContent = '❌';
      statusMessage.textContent = `Error: ${error.message}`;
      statusLink.classList.add('hidden');
    } finally {
      // Re-enable button
      generateBtn.disabled = false;
      btnText.classList.remove('hidden');
      btnSpinner.classList.add('hidden');
    }
  });

  // ---------- Polling logic ----------
  async function pollForCompletion(runId, maxAttempts = 120, intervalMs = 15000) {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      // Wait interval
      await new Promise(resolve => setTimeout(resolve, intervalMs));

      try {
        const checkResp = await fetch(`${API_BASE}/check-status?run_id=${runId}`);
        if (!checkResp.ok) {
          const errText = await checkResp.text();
          console.warn(`Status check failed: ${errText}`);
          continue; // try again
        }

        const data = await checkResp.json();
        const { status, conclusion, download_url } = data;

        // Update UI
        statusIcon.textContent = status === 'completed' ? '✅' : '⏳';
        if (status === 'completed') {
          statusMessage.textContent = 'Video ready!';
          statusLink.classList.add('hidden');

          if (download_url) {
            downloadSection.classList.remove('hidden');
            downloadLink.href = download_url;
          } else {
            statusMessage.textContent = 'Completed, but no download URL found.';
          }
          return; // done
        } else if (status === 'queued' || status === 'in_progress') {
          statusMessage.textContent = `Generating video... (attempt ${attempt})`;
        } else {
          // failure, cancelled, etc.
          statusMessage.textContent = `Workflow ${status}${conclusion ? ': ' + conclusion : ''}.`;
          statusLink.classList.add('hidden');
          return;
        }
      } catch (err) {
        console.error('Polling error:', err);
        // continue polling on network errors
      }
    }

    // Timeout
    statusIcon.textContent = '⏰';
    statusMessage.textContent = 'Video is taking longer than expected. Please check the workflow run manually.';
  }
})();