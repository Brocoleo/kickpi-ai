import { ChatIcon, DeviceIcon, FolderIcon, PulseIcon, SlidersIcon } from "./icons";

export type Tab = "chat" | "metrics" | "equipos" | "files" | "settings";

const TABS: { id: Tab; label: string; Icon: typeof ChatIcon }[] = [
  { id: "chat", label: "Chat", Icon: ChatIcon },
  { id: "metrics", label: "Métricas", Icon: PulseIcon },
  { id: "equipos", label: "Equipos", Icon: DeviceIcon },
  { id: "files", label: "Archivos", Icon: FolderIcon },
  { id: "settings", label: "Ajustes", Icon: SlidersIcon },
];

type Props = {
  tab: Tab;
  onChange: (tab: Tab) => void;
};

export function TabBar({ tab, onChange }: Props) {
  return (
    <nav className="tabbar" aria-label="Secciones">
      {TABS.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          className={`tab ${tab === id ? "active" : ""}`}
          onClick={() => onChange(id)}
          aria-current={tab === id ? "page" : undefined}
        >
          <Icon />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}
