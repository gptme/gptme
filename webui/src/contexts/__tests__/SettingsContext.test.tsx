import '@testing-library/jest-dom';
import React, { useEffect } from 'react';
import { act, render, renderHook } from '@testing-library/react';
import { SettingsProvider, useSettings } from '../SettingsContext';
import { stopSpeaking } from '../../utils/tts';

jest.mock('../../utils/tts', () => ({ stopSpeaking: jest.fn() }));

const wrapper = ({ children }: React.PropsWithChildren) => (
  <SettingsProvider>{children}</SettingsProvider>
);

beforeEach(() => {
  localStorage.clear();
  jest.clearAllMocks();
});

it('keeps updater identity across updates while persisting fresh state', () => {
  const { result } = renderHook(useSettings, { wrapper });
  const update = result.current.updateSettings;
  act(() => update({ hasCompletedSetup: true }));
  expect(result.current.updateSettings).toBe(update);
  act(() => {
    update({ ttsAuthToken: 'first' });
    update({ sttAuthToken: 'second' });
  });
  expect(result.current.settings).toMatchObject({
    hasCompletedSetup: true,
    ttsAuthToken: 'first',
    sttAuthToken: 'second',
  });
  expect(JSON.parse(localStorage.getItem('gptme-settings')!)).toMatchObject(
    result.current.settings
  );
  act(() => update({ ttsEnabled: false }));
  expect(stopSpeaking).toHaveBeenCalledTimes(1);
});

it('settles effect-driven token sync and handles refresh and logout', () => {
  let calls = 0;
  function TokenSync({ token }: { token: string | null }) {
    const { updateSettings } = useSettings();
    useEffect(() => {
      // Hard budget makes a callback feedback regression fail without hanging Jest.
      calls++;
      if (calls > 8) throw new Error('settings-update-budget-exceeded');
      updateSettings({
        ttsAuthToken: token ?? '',
        sttAuthToken: token ?? '',
        sttProvider: token ? 'server' : 'browser',
      });
    }, [token, updateSettings]);
    return null;
  }
  const tree = (token: string | null) => (
    <SettingsProvider>
      <TokenSync token={token} />
    </SettingsProvider>
  );
  const { rerender } = render(tree('first'));
  expect(calls).toBe(1);
  rerender(tree('refreshed'));
  expect(calls).toBe(2);
  expect(JSON.parse(localStorage.getItem('gptme-settings')!)).toMatchObject({
    ttsAuthToken: 'refreshed',
    sttAuthToken: 'refreshed',
    sttProvider: 'server',
  });
  rerender(tree(null));
  expect(calls).toBe(3);
  expect(JSON.parse(localStorage.getItem('gptme-settings')!)).toMatchObject({
    ttsAuthToken: '',
    sttAuthToken: '',
    sttProvider: 'browser',
  });
});
