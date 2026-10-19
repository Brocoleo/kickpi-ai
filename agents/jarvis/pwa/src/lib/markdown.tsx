import type { ReactNode } from "react";

/** Renderizador mínimo: párrafos, listas, tablas, **negrita** y `código`. */
function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return <code key={key}>{part.slice(1, -1)}</code>;
    }
    return <span key={key}>{part}</span>;
  });
}

function isSeparator(line: string): boolean {
  return /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line) && line.includes("-");
}

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

export function RichText({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  let blockIndex = 0;

  const flushList = () => {
    if (list.length === 0) return;
    const items = list;
    list = [];
    blocks.push(
      <ul key={`ul-${blockIndex++}`}>
        {items.map((item, i) => (
          <li key={i}>{inline(item, `li-${blockIndex}-${i}`)}</li>
        ))}
      </ul>,
    );
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];

    // Tabla: fila con "|" seguida de separador "|---|".
    if (
      line.includes("|") &&
      index + 1 < lines.length &&
      isSeparator(lines[index + 1])
    ) {
      flushList();
      const header = cells(line);
      const rows: string[][] = [];
      let cursor = index + 2;
      while (cursor < lines.length && lines[cursor].includes("|")) {
        rows.push(cells(lines[cursor]));
        cursor += 1;
      }
      const tableKey = blockIndex++;
      blocks.push(
        <div className="table-wrap" key={`table-${tableKey}`}>
          <table>
            <thead>
              <tr>
                {header.map((cell, i) => (
                  <th key={i}>{inline(cell, `th-${tableKey}-${i}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, i) => (
                    <td key={i}>{inline(cell, `td-${tableKey}-${rowIndex}-${i}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      index = cursor - 1;
      continue;
    }

    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }

    flushList();
    if (line.trim() === "") continue;
    blocks.push(<p key={`p-${blockIndex++}`}>{inline(line, `p-${blockIndex}`)}</p>);
  }
  flushList();

  return <>{blocks}</>;
}
