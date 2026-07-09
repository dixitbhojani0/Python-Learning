import { MarkdownPipe } from './markdown.pipe';

describe('MarkdownPipe', () => {
  // bypassSecurityTrustHtml just needs to hand the raw HTML string back so we can
  // assert on it directly — the real sanitizer isn't under test here.
  const fakeSanitizer = { bypassSecurityTrustHtml: (html: string) => html } as any;
  const pipe = new MarkdownPipe(fakeSanitizer);

  it('keeps ordered-list numbering continuous across ul sub-bullet interruptions', () => {
    const md = [
      '1. Maintain a Clean .gitignore',
      '',
      '- Regularly update your .gitignore.',
      '',
      '1. Branch Management',
      '',
      '- Initiate a new branch per task.',
      '',
      '1. Daily Commit Routine',
      '',
      '- Commit and push daily.',
    ].join('\n');

    const html = pipe.transform(md) as unknown as string;

    expect(html).toContain('<ol><li>Maintain a Clean .gitignore</li></ol>');
    expect(html).toContain('<ol start="2"><li>Branch Management</li></ol>');
    expect(html).toContain('<ol start="3"><li>Daily Commit Routine</li></ol>');
  });

  it('resets numbering after a heading (real section break)', () => {
    const md = ['## Basic', '1. A', '2. B', '## Advanced', '1. C'].join('\n');
    const html = pipe.transform(md) as unknown as string;

    expect(html).toContain('<ol><li>A</li><li>B</li></ol>');
    expect(html).toContain('<ol><li>C</li></ol>');
    expect(html).not.toContain('start="3"');
  });

  it('renders a GFM table (the edit/assign-ticket proposal card shape)', () => {
    const md = [
      '| | |',
      '|---|---|',
      '| Current | v2.2 introduces two additions |',
      '| New | v2.3 introduces two additions |',
    ].join('\n');
    const html = pipe.transform(md) as unknown as string;

    expect(html).toContain('<table>');
    expect(html).toContain('<td>Current</td><td>v2.2 introduces two additions</td>');
    expect(html).toContain('<td>New</td><td>v2.3 introduces two additions</td>');
    expect(html).toContain('</tbody></table>');
    expect(html).not.toContain('|---|');
  });

  it('closes a table and resumes normal parsing for the following content', () => {
    const md = ['| Field | Value |', '|---|---|', '| A | B |', '', 'after the table'].join('\n');
    const html = pipe.transform(md) as unknown as string;

    expect(html).toContain('<table>');
    expect(html).toContain('<p>after the table</p>');
  });

  it('renders "> text" as a real blockquote instead of a literal ">" (the hitl.py confirmation card shape)', () => {
    const md = ['Title changed to:', '', '> Create Verification Test for Fix-3', '', 'Approved by: Bob.'].join('\n');
    const html = pipe.transform(md) as unknown as string;

    expect(html).toContain('<blockquote><p>Create Verification Test for Fix-3</p></blockquote>');
    expect(html).not.toContain('&gt;');
    expect(html).not.toMatch(/<p>>\s/);
    expect(html).toContain('<p>Approved by: Bob.</p>');
  });

  it('merges consecutive ">" lines into one blockquote', () => {
    const md = ['> line one', '> line two'].join('\n');
    const html = pipe.transform(md) as unknown as string;

    expect(html).toBe('<blockquote><p>line one</p><p>line two</p></blockquote>');
  });

  it('renders a fenced code block as <pre><code> without reinterpreting its content', () => {
    const md = [
      'Add this to nginx.conf:',
      '',
      '```nginx',
      'add_header Access-Control-Allow-Origin * always;',
      '- this looks like a bullet but is code',
      '```',
      '',
      'Then reload nginx.',
    ].join('\n');
    const html = pipe.transform(md) as unknown as string;

    expect(html).toContain(
      '<pre><code>add_header Access-Control-Allow-Origin * always;\n- this looks like a bullet but is code</code></pre>',
    );
    expect(html).not.toContain('<li>'); // the "- " line inside the fence must NOT become a bullet
    expect(html).toContain('<p>Then reload nginx.</p>'); // parsing resumes normally after the fence
  });

  it('does not apply inline emphasis to asterisks/backticks inside a code fence', () => {
    const md = ['```', 'print("*not bold*, `not inline code`")', '```'].join('\n');
    const html = pipe.transform(md) as unknown as string;

    expect(html).toContain('<pre><code>print("*not bold*, `not inline code`")</code></pre>');
    expect(html).not.toContain('<strong>');
    expect(html).not.toContain('<em>');
  });
});
