import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

/**
 * Converts the subset of Markdown used by our LLM responses into HTML.
 * Covers: headings, bold, italic, inline code, code fences, bullets, numbered lists,
 * tables, blockquotes, and paragraphs.
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
    let inTable = false;
    let inBq = false;
    // Running count of ordered-list items emitted so far. A ul sub-bullet (or a
    // blank line) between two ol items closes and reopens a brand-new <ol> element
    // — with no memory of the last one, the browser numbers it from 1 again. Track
    // the count ourselves and reopen with start="N+1" so the visible numbering
    // (1, 2, 3, 4...) survives the interruption. Only a heading/hr is a real
    // section break, so only those reset it.
    let olCount = 0;

    const closeList = () => {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
      if (inBq) { out.push('</blockquote>'); inBq = false; }
    };
    const splitRow = (line: string): string[] =>
      line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());

    let i = 0;
    while (i < lines.length) {
      const line = lines[i].trimEnd();

      // Fenced code block: ```lang ... ```. Checked first and consumed in one shot
      // (its own closing marker, not the outer loop's per-line state machine) because
      // content inside must NOT be reinterpreted as headings/bullets/tables/inline
      // emphasis — a YAML snippet's "- key: value" lines are not a markdown bullet list,
      // and literal *asterisks*/`backticks` in code must render as-is, not as emphasis.
      if (/^```/.test(line)) {
        closeList();
        const codeLines: string[] = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i].trimEnd())) {
          codeLines.push(lines[i]);
          i++;
        }
        i++; // consume the closing ``` (or run off the end if the fence was never closed)
        out.push(`<pre><code>${codeLines.join('\n')}</code></pre>`);
        continue;
      }

      // GFM table: a "| a | b |" header row immediately followed by a
      // "|---|---|" delimiter row. Emitted as a real <table> — proposal cards
      // (assign/edit ticket, etc.) format their diff as a table, which this
      // renderer previously had no support for at all (showed as raw "| | |" text).
      if (inTable) {
        if (/^\|.+\|$/.test(line.trim())) {
          const cells = splitRow(line);
          out.push('<tr>' + cells.map(c => `<td>${this.inline(c)}</td>`).join('') + '</tr>');
          i++;
          continue;
        }
        out.push('</tbody></table>');
        inTable = false;
        // fall through — this line is not a table row, process normally below
      } else if (/^\|.+\|$/.test(line.trim())) {
        const next = (lines[i + 1] || '').trim();
        if (/^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(next)) {
          closeList();
          const headers = splitRow(line);
          out.push('<table><thead><tr>' + headers.map(h => `<th>${this.inline(h)}</th>`).join('') + '</tr></thead><tbody>');
          inTable = true;
          i += 2; // consume header row + delimiter row
          continue;
        }
      }

      // Headings
      const h = line.match(/^(#{1,4})\s+(.+)/);
      if (h) {
        closeList();
        olCount = 0;
        const level = h[1].length;
        out.push(`<h${level}>${this.inline(h[2])}</h${level}>`);
        i++; continue;
      }

      // Blockquote: "> text" — used for quoted values in proposal/confirmation cards
      // (e.g. hitl.py's `f"> {new_value}"`). Consecutive '>' lines merge into one
      // <blockquote> rather than one per line.
      const bq = line.match(/^>\s?(.*)$/);
      if (bq) {
        if (!inBq) { closeList(); out.push('<blockquote>'); inBq = true; }
        out.push(`<p>${this.inline(bq[1])}</p>`);
        i++; continue;
      }

      // Unordered list item
      const ul = line.match(/^[-*]\s+(.+)/);
      if (ul) {
        if (!inUl) { closeList(); out.push('<ul>'); inUl = true; }
        out.push(`<li>${this.inline(ul[1])}</li>`);
        i++; continue;
      }

      // Ordered list item
      const ol = line.match(/^\d+\.\s+(.+)/);
      if (ol) {
        if (!inOl) { closeList(); out.push(olCount > 0 ? `<ol start="${olCount + 1}">` : '<ol>'); inOl = true; }
        out.push(`<li>${this.inline(ol[1])}</li>`);
        olCount++;
        i++; continue;
      }

      // Horizontal rule
      if (/^---+$/.test(line)) {
        closeList();
        olCount = 0;
        out.push('<hr>');
        i++; continue;
      }

      // Blank line — paragraph break, UNLESS we're inside a list/blockquote. LLM output
      // often puts a blank line between list items for readability; closing
      // the list there would split one ordered list into several single-item
      // lists, each restarting its own numbering at "1."
      if (line === '') {
        if (inUl || inOl || inBq) { i++; continue; }
        out.push('<br>');
        i++; continue;
      }

      // Normal text
      closeList();
      out.push(`<p>${this.inline(line)}</p>`);
      i++;
    }

    closeList();
    if (inTable) out.push('</tbody></table>');
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
