// @vitest-environment jsdom
import {act, fireEvent, render, screen, cleanup} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {GrafanaEmbed} from './GrafanaEmbed';
afterEach(()=>{vi.useRealTimers(); cleanup()});
describe('GrafanaEmbed',()=>{
  it('does not claim authenticated readiness on iframe load',()=>{
    render(<GrafanaEmbed />);
    fireEvent.load(screen.getByTitle('Rolling 24-hour telemetry'));
    expect(screen.getByText(/A loaded frame does not prove/)).toBeTruthy();
    expect(screen.queryByText('Loading Grafana dashboard…')).toBeNull();
  });
  it('shows a bounded timeout and resets when device URL changes',()=>{
    vi.useFakeTimers();
    const {rerender}=render(<GrafanaEmbed variables={{'var-device':'A'}} />);
    act(()=>vi.advanceTimersByTime(20000));
    expect(screen.getByRole('alert').textContent).toContain('20 seconds');
    rerender(<GrafanaEmbed variables={{'var-device':'B'}} />);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('Loading Grafana dashboard…')).toBeTruthy();
  });
});
