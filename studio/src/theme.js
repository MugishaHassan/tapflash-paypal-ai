// TapFlash theme for AG Studio: the same colours and type as the rest of the operators' pages.
import { studioTheme } from 'ag-studio';

const series = ['#1C1E49', '#5CC4DC', '#5d6290', '#fab219', '#ec835a', '#0ca30c', '#a9b1ba', '#d03b3b', '#262A5E', '#79D3E7'];
const palette = Object.fromEntries(series.flatMap((c, i) => [[`chartPaletteFills${i + 1}Color`, c], [`chartPaletteStrokes${i + 1}Color`, c]]));

export const tapflashTheme = studioTheme.withParams({
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  fontSize: 14,
  textColor: '#0b1f2c',
  foregroundColor: '#0b1f2c',
  subtleTextColor: '#4b5d68',
  backgroundColor: '#fcfcfb',
  borderColor: '#e3e7ea',
  accentColor: '#1C1E49',
  iconColor: '#1C1E49',
  rowHoverColor: '#eef7fa',
  studioWrapperBorder: false,
  studioWrapperBorderRadius: 0,
  studioWrapperSpacing: 0,
  studioWrapperBackgroundColor: '#f4f6f8',
  studioCanvasBorder: false,
  studioCanvasBorderRadius: 0,
  studioCanvasShadow: false,
  studioCanvasBackgroundColor: '#f4f6f8',
  ...palette,
});
