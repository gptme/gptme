import React, { createContext, useContext, useState } from 'react';

export interface Settings {
  chimeEnabled: boolean;
  blocksDefaultOpen: boolean;
  showHiddenMessages: boolean;
  showInitialSystem: boolean;
  hasCompletedSetup: boolean;
  /** CSS background for the welcome/new-chat view (image URL or gradient) */
  welcomeBackground: string;
}

interface SettingsContextType {
  settings: Settings;
  updateSettings: (updates: Partial<Settings>) => void;
  resetSettings: () => void;
}

const defaultSettings: Settings = {
  chimeEnabled: true,
  blocksDefaultOpen: true,
  showHiddenMessages: false,
  showInitialSystem: false,
  hasCompletedSetup: false,
  welcomeBackground: '',
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
