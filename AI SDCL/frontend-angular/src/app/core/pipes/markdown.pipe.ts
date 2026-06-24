import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

/**
 * Converts the subset of Markdown used by our LLM responses into HTML.
 * Covers: headings, bold, italic, inline code, bullets, numbered lists, and paragraphs.
 * Deliberately minimal — a full CommonMark parser is overkill for chat bubbles.
 */
@Pipe({ name: 'markdown', standalone: true })
export class MarkdownPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string): SafeHtml {
    return this.sanitizer.bypassSecurityTrustHtml(value ? this.parse(value) : '');
  }

  private parse(md: string): string {
    const lines  = md.split('\n');
    const out: string[] = [];
    let inUl = false;
    let inOl = false;

    const closeList = () => {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
    };

    for (const raw of lines) {
      const line = raw.trimEnd();

      // Headings
      const h = line.match(/^(#{1,4})\s+(.+)/);
      if (h) {
        closeList();
        const level = h[1].length;
        out.push(`<h${level}>${this.inline(h[2])}</h${level}>`);
        continue;
      }

      // Unordered list item
      const ul = line.match(/^[-*]\s+(.+)/);
      if (ul) {
        if (!inUl) { closeList(); out.push('<ul>'); inUl = true; }
        out.push(`<li>${this.inline(ul[1])}</li>`);
        continue;
      }

      // Ordered list item
      const ol = line.match(/^\d+\.\s+(.+)/);
      if (ol) {
        if (!inOl) { closeList(); out.push('<ol>'); inOl = true; }
        out.push(`<li>${this.inline(ol[1])}</li>`);
        continue;
      }

      // Horizontal rule
      if (/^---+$/.test(line)) {
        closeList();
        out.push('<hr>');
        continue;
      }

      // Blank line — paragraph break
      if (line === '') {
        closeList();
        out.push('<br>');
        continue;
      }

      // Normal text
      closeList();
      out.push(`<p>${this.inline(line)}</p>`);
    }

    closeList();
    return out.join('');
  }

  private inline(text: string): string {
    return text
      .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*(.+?)\*\*/g,     '<strong>$1</strong>')
      .replace(/__(.+?)__/g,          '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g,          '<em>$1</em>')
      .replace(/_(.+?)_/g,            '<em>$1</em>')
      .replace(/`(.+?)`/g,            '<code>$1</code>');
  }
}
