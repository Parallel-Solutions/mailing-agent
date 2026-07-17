declare module 'grapesjs-preset-newsletter' {
  import type { Editor } from 'grapesjs';

  type NewsletterPresetPlugin = (editor: Editor, options?: Record<string, unknown>) => void;

  const plugin: { default: NewsletterPresetPlugin };
  export default plugin;
}
