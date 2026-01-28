/**
 * Audio utilities for playing notification sounds
 */

// Extend Window interface to include webkit-prefixed AudioContext
declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext;
  }
}

let audioContext: AudioContext | null = null;
let chimeBuffer: AudioBuffer | null = null;

/**
 * Initialize audio context and load the chime sound
 */
async function initializeAudio(): Promise<void> {
  if (audioContext && chimeBuffer) {
    return; // Already initialized
  }

  try {
    // Create audio context
    audioContext = new (window.AudioContext || window.webkitAudioContext)();

    // Load the chime sound file
    const response = await fetch('/chime.mp3');
    const arrayBuffer = await response.arrayBuffer();
    chimeBuffer = await audioContext.decodeAudioData(arrayBuffer);

    console.log('Audio initialized successfully');
  } catch (error) {
    console.error('Failed to initialize audio:', error);
  }
}

/**
 * Check if chime is enabled in settings
 */
function isChimeEnabled(): boolean {
  try {
    const savedSettings = localStorage.getItem('gptme-settings');
    if (savedSettings) {
      const settings = JSON.parse(savedSettings);
      return settings.chimeEnabled !== false; // Default to true if not set
    }
    return true; // Default to enabled
  } catch (error) {
    console.error('Failed to read chime setting:', error);
    return true; // Default to enabled on error
  }
}

/**
 * Play the chime sound
 */
export async function playChime(): Promise<void> {
  try {
    // Check if chime is enabled
    if (!isChimeEnabled()) {
      console.log('Chime disabled, skipping');
      return;
    }

    // Initialize audio if needed
    await initializeAudio();

    if (!audioContext || !chimeBuffer) {
      console.warn('Audio not initialized, cannot play chime');
      return;
    }

    // Resume audio context if it's suspended (required by some browsers)
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }

    // Create audio nodes for volume control
    const source = audioContext.createBufferSource();
    const gainNode = audioContext.createGain();

    // Set volume to 50% of original
    gainNode.gain.value = 0.5;

    source.buffer = chimeBuffer;
    source.connect(gainNode);
    gainNode.connect(audioContext.destination);
    source.start(0);

    console.log('Chime played');
  } catch (error) {
    console.error('Failed to play chime:', error);
  }
}

/**
 * Check if audio is supported
 */
export function isAudioSupported(): boolean {
  return !!(window.AudioContext || window.webkitAudioContext);
}
