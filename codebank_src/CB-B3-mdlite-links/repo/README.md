# mdlite

The tiny Markdown renderer behind the family chat's message bubbles. One ES
module, no dependencies: `render(text) -> html string`. Messages come from
anyone on the family network, so the output must be safe to put in
`innerHTML`.

Supported today: paragraphs (split on blank lines, single newlines become
`<br>`), `**bold**`, `*em*`, `` `code` `` (nothing inside code is formatted),
and escaping of `& < > " '` everywhere.

## Links (to add in this release)

`[text](url)` becomes `<a href="URL" rel="nofollow noopener">TEXT</a>`:

* TEXT gets bold and em like any other text (it may not contain a code
  span or `]`); URL is escaped
  like any other text (so `&` becomes `&amp;` and `"` becomes `&quot;`).
* The URL is everything between the parentheses; it may not be empty and may
  not contain whitespace or `)`. Otherwise the brackets are plain text.
* Safety: if the URL starts with a scheme (letters, digits, `+ - .`, beginning with a letter,
  then `:`), the scheme must be `http`, `https` or `mailto`, in any letter
  case. URLs without a scheme (`/x`, `./x`, `#top`, `page.html`) are allowed.
  A link with any other scheme renders as its formatted TEXT only, no `<a>`.
* Inside a code span nothing is a link.
