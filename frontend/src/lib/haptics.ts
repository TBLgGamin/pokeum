/** Short vibration feedback where the device supports it (most Android phones; iOS ignores it). */
export function haptic(pattern: number | number[]): void {
  navigator.vibrate?.(pattern);
}
