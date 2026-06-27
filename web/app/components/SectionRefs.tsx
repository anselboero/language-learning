import Link from "next/link";

// Renders GGU references like "1.1.1–1.1.9" or "12.3" as links.
// Ranges link both endpoints with the dash preserved between them.

function refToLinks(ref: string, key: number) {
  const dash = ref.match(/[–—-]/);
  if (dash) {
    const [start, end] = ref.split(/[–—-]/).map((s) => s.trim());
    return (
      <span key={key}>
        <Link href={`/sections/${start}`}>§{start}</Link>
        {"–"}
        <Link href={`/sections/${end}`}>§{end}</Link>
      </span>
    );
  }
  const n = ref.trim();
  return (
    <Link key={key} href={`/sections/${n}`}>
      §{n}
    </Link>
  );
}

export default function SectionRefs({ refs }: { refs: string[] }) {
  if (refs.length === 0) return null;
  return (
    <span>
      {refs.map((ref, i) => (
        <span key={i}>
          {i > 0 && ", "}
          {refToLinks(ref, i)}
        </span>
      ))}
    </span>
  );
}
