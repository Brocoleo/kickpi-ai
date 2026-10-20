import { ChatIcon, DeviceIcon, FolderIcon, PulseIcon, ShieldCheckIcon, SlidersIcon } from "./icons";

export type Tab = "chat" | "metrics" | "equipos" | "files" | "approvals" | "settings";

const TABS: { id: Tab; label: string; Icon: typeof ChatIcon }[] = [
  { id: "chat", label: "Chat", Icon: ChatIcon },
  { id: "metrics", label: "Métricas", Icon: PulseIcon },
  { id: "equipos", label: "Equipos", Icon: DeviceIcon },
  { id: "files", label: "Archivos", Icon: FolderIcon },
  { id: "approvals", label: "Aprobaciones", Icon: ShieldCheckIcon },
  { id: "settings", label: "Ajustes", Icon: SlidersIcon },
];

type Props = {
  tab: Tab;
  onChange: (tab: Tab) => void;
  /** Solicitudes de aprobacion pendientes (contador del badge). */
  pendingCount?: number;
};

export function TabBar({ tab, onChange, pendingCount = 0 }: Props) {
  return (
    <nav className="tabbar" aria-label="Secciones">
      {TABS.map(({ id, label, Icon }) => {
        const isApprovals = id === "approvals";
        const badge = isApprovals && pendingCount > 0 ? pendingCount : 0;
        const ariaLabel =
          isApprovals && badge > 0
            ? `${label}: ${badge} solicitud${badge === 1 ? "" : "es"} pendiente${badge === 1 ? "" : "s"}`
            : label;
        return (
          <button
            key={id}
            type="button"
            className={`tab ${tab === id ? "active" : ""}`}
            onClick={() => onChange(id)}
            aria-current={tab === id ? "page" : undefined}
            aria-label={ariaLabel}
          >
            <span className="tab-icon">
              <Icon />
              {badge > 0 && (
                <span className="tab-badge" aria-hidden="true">
                  {badge > 9 ? "9+" : badge}
                </span>
              )}
            </span>
            <span>{label}</span>
          </button>
        );
      })}
    </nav>
  );
}
