import { Pipe, PipeTransform } from '@angular/core';

/**
 * Lightweight inline formatter for AI answer text: turns `**bold**` into
 * <strong> and `` `code` `` into <code> so answers read like a well-formatted
 * ChatGPT reply. Everything else is HTML-escaped first, and Angular's DOM
 * sanitizer runs on the final string (used with [innerHTML]), so there is no
 * injection surface.
 */
@Pipe({ name: 'richText', standalone: true })
export class RichTextPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) return '';
    const escaped = value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    // Inline code first (backticks), then bold (double asterisks).
    return escaped
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  }
}
