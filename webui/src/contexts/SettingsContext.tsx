import React, { createContext, useContext, useEffect, useState } from 'react';

export interface Settings {
  chimeEnabled: boolean;
  blocksDefaultOpen: boolean;
  showHiddenMessages: boolean;
  showInitialSystem: boolean;
  hasCompletedSetup: boolean;
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
};

const SettingsContext = createContext<SettingsContextType | undefined>(undefined);

export const useSettings = () => {
  const context = useContext(SettingsContext);
  if (!context) {
    throw new Error('useSettings must be used within a SettingsProvider');
  }
  return context;
};

export const SettingsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [settings, setSettings] = useState<Settings>(defaultSettings);

  // Load settings from localStorage on mount
  useEffect(() => {
    try {
      const savedSettings = localStorage.getItem('gptme-settings');
      if (savedSettings) {
        const parsed = JSON.parse(savedSettings);
        // Existing users who pre-date hasCompletedSetup should not see the wizard
        const hasCompletedSetup = parsed.hasCompletedSetup ?? true;
        setSettings({ ...defaultSettings, ...parsed, hasCompletedSetup });
      }
    } catch (error) {
      console.error('Failed to load settings from localStorage:', error);
    }
  }, []);

  const updateSettings = (updates: Partial<Settings>) => {
    const newSettings = { ...settings, ...updates };
    setSettings(newSettings);

    try {
      localStorage.setItem('gptme-settings', JSON.stringify(newSettings));
    } catch (error) {
      console.error('Failed to save settings to localStorage:', error);
    }
  };

  const resetSettings = () => {
    // Preserve hasCompletedSetup so a settings reset doesn't re-trigger the wizard
    const preserveSetup = settings.hasCompletedSetup;
    setSettings({ ...defaultSettings, hasCompletedSetup: preserveSetup });
    try {
      localStorage.removeItem('gptme-settings');
    } catch (error) {
      console.error('Failed to reset settings in localStorage:', error);
    }
  };

  return (
    <SettingsContext.Provider value={{ settings, updateSettings, resetSettings }}>
      {children}
    </SettingsContext.Provider>
  );
};
