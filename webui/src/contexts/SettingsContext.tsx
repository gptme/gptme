import React, { createContext, useContext, useState } from 'react';
import { stopSpeaking, type TtsProvider } from '../utils/tts';

export interface Settings {
  chimeEnabled: boolean;
  ttsEnabled: boolean;
  /**
   * Which TTS engine to use:
   * - 'auto': try the gptme-server endpoint, then an external gptme-tts server, then browser
   * - 'browser': the browser's built-in speechSynthesis
   * - 'server': the connected gptme-server's /api/v2/audio/speech (provider-backed, e.g. OpenRouter)
   * - 'external': a standalone gptme-tts server at `ttsServerUrl`
   */
  ttsProvider: TtsProvider;
  /**
   * Which STT engine to use:
   * - 'browser': use the browser's built-in SpeechRecognition API (fall back to server if unavailable)
   * - 'server': always use the server-side transcription via MediaRecorder + /api/v2/audio/transcriptions
   */
  sttProvider: 'browser' | 'server';
  blocksDefaultOpen: boolean;
  showHiddenMessages: boolean;
  showInitialSystem: boolean;
  hasCompletedSetup: boolean;
  /** CSS background for the welcome/new-chat view (image URL or gradient) */
  welcomeBackground: string;
  /**
   * HTTP base URL of a running gptme-tts server, e.g. http://localhost:5701.
   * When set, TTS uses this server instead of the browser's speechSynthesis API.
   * Leave empty to use browser TTS.
   */
  ttsServerUrl: string;
  /**
   * Bearer token sent with /api/v2/audio/speech requests.
   * Set by cloud hosts (e.g. gptme.ai) so TTS can be billed to the user's account.
   * Leave empty for self-hosted / unauthenticated endpoints.
   *
   * Token lifecycle: cloud hosts MUST call `updateSettings({ ttsAuthToken: '' })` on
   * logout to clear this from localStorage. The token persists across page reloads
   * intentionally (to avoid re-authentication overhead), so explicit logout cleanup is
   * required.
   */
  ttsAuthToken: string;
  /**
   * Bearer token sent with /api/v2/audio/transcriptions requests.
   * Set by cloud hosts (e.g. gptme.ai) so STT can be billed to the user's account.
   * When set, STT uses the same-origin /api/v2/audio/transcriptions endpoint
   * (e.g. a Cloudflare Pages Function proxy) instead of the connected gptme server.
   * Leave empty for self-hosted / unauthenticated endpoints.
   *
   * Token lifecycle: cloud hosts MUST call `updateSettings({ sttAuthToken: '' })` on
   * logout to clear this from localStorage.
   */
  sttAuthToken: string;
  /**
   * WebSocket URL for the gptme-voice-server /voice endpoint, e.g. ws://localhost:5700/voice.
   * Leave empty to hide the VoiceButton.
   */
  voiceServerUrl: string;
  /**
   * When true, tool execution prompts are skipped and all tools are auto-confirmed.
   * Equivalent to --no-confirm in the CLI (also known as "YOLO mode").
   */
  noConfirmMode: boolean;
}

interface SettingsContextType {
  settings: Settings;
  updateSettings: (updates: Partial<Settings>) => void;
  resetSettings: () => void;
}

const defaultSettings: Settings = {
  chimeEnabled: true,
  ttsEnabled: false,
  ttsProvider: 'auto',
  sttProvider: 'browser',
  blocksDefaultOpen: true,
  showHiddenMessages: false,
  showInitialSystem: false,
  hasCompletedSetup: false,
  welcomeBackground: '',
  ttsServerUrl: '',
  ttsAuthToken: '',
  sttAuthToken: '',
  voiceServerUrl: '',
  noConfirmMode: false,
};

function loadSettingsFromStorage(): Settings {
  try {
    const savedSettings = localStorage.getItem('gptme-settings');
    if (savedSettings) {
      const parsed = JSON.parse(savedSettings);
      // Existing users who pre-date hasCompletedSetup should not see the wizard
      const hasCompletedSetup = parsed.hasCompletedSetup ?? true;
      return { ...defaultSettings, ...parsed, hasCompletedSetup };
    }
  } catch (error) {
    console.error('Failed to load settings from localStorage:', error);
  }
  return defaultSettings;
}

const SettingsContext = createContext<SettingsContextType | undefined>(undefined);

export const useSettings = () => {
  const context = useContext(SettingsContext);
  if (!context) {
    throw new Error('useSettings must be used within a SettingsProvider');
  }
  return context;
};

export const SettingsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Initialize synchronously from localStorage to prevent a flash of the setup wizard
  const [settings, setSettings] = useState<Settings>(loadSettingsFromStorage);

  const updateSettings = (updates: Partial<Settings>) => {
    if (updates.ttsEnabled === false) {
      stopSpeaking();
    }
    // Use functional updater to avoid stale closure if called in rapid succession.
    setSettings((current) => {
      const newSettings = { ...current, ...updates };
      try {
        localStorage.setItem('gptme-settings', JSON.stringify(newSettings));
      } catch (error) {
        console.error('Failed to save settings to localStorage:', error);
      }
      return newSettings;
    });
  };

  const resetSettings = () => {
    stopSpeaking();
    // Use functional updater to avoid stale closure on hasCompletedSetup.
    // Preserve hasCompletedSetup so a settings reset doesn't re-trigger the wizard.
    setSettings((current) => {
      const newSettings = { ...defaultSettings, hasCompletedSetup: current.hasCompletedSetup };
      try {
        localStorage.setItem('gptme-settings', JSON.stringify(newSettings));
      } catch (error) {
        console.error('Failed to reset settings in localStorage:', error);
      }
      return newSettings;
    });
  };

  return (
    <SettingsContext.Provider value={{ settings, updateSettings, resetSettings }}>
      {children}
    </SettingsContext.Provider>
  );
};
