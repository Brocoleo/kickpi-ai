import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Settings } from "./Settings";
import { ApiError } from "../api";

const apiMock = vi.hoisted(() => ({
  llmStatus: vi.fn(),
  health: vi.fn(),
  nasConfig: vi.fn(),
  updateNasConfig: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: apiMock,
    ApiError: actual.ApiError,
  };
});

const ME = {
  authenticated: true,
  method: "session" as const,
  password_configured: true,
  session_ttl_days: 30,
  version: "0.18.0",
};

const CONFIG = {
  inbox: "files/Entrada",
  inbox_dir: "/mnt/data/nas/files/Entrada",
  upload_max_mb: 50,
  upload_rate_per_min: 60,
  classifier_rules: null,
};

beforeEach(() => {
  Object.values(apiMock).forEach((fn) => fn.mockReset());
  apiMock.llmStatus.mockResolvedValue({
    configured: true,
    base_url: "https://openrouter.ai/api/v1",
    models: ["modelo-principal"],
    circuit_breaker: {},
    policy: {},
  });
  apiMock.health.mockResolvedValue({ static_asset: "index-abc.js" });
  apiMock.nasConfig.mockResolvedValue(CONFIG);
  apiMock.updateNasConfig.mockResolvedValue(CONFIG);
});

function renderSettings() {
  return render(
    <Settings
      me={ME}
      csrfToken="csrf-9"
      onLogout={() => {}}
      onUnauthorized={() => {}}
    />,
  );
}

describe("Ajustes · NAS", () => {
  it("muestra la configuración vigente (incluida la ruta de entrada)", async () => {
    renderSettings();

    expect(await screen.findByDisplayValue("files/Entrada")).toBeInTheDocument();
    expect(screen.getByDisplayValue("50")).toBeInTheDocument();
    expect(screen.getByDisplayValue("60")).toBeInTheDocument();
  });

  it("guarda los límites con CSRF y muestra confirmación", async () => {
    const user = userEvent.setup();
    renderSettings();

    const maximo = await screen.findByDisplayValue("50");
    await user.clear(maximo);
    await user.type(maximo, "80");
    await user.click(
      screen.getByRole("button", { name: "Guardar ajustes del NAS" }),
    );

    await waitFor(() =>
      expect(apiMock.updateNasConfig).toHaveBeenCalledWith(
        {
          upload_max_mb: 80,
          upload_rate_per_min: 60,
          classifier_rules: "",
        },
        "csrf-9",
      ),
    );
    expect(await screen.findByText("guardado")).toBeInTheDocument();
  });

  it("no llama al servidor si el límite no es un entero válido", async () => {
    const user = userEvent.setup();
    renderSettings();

    const maximo = await screen.findByDisplayValue("50");
    await user.clear(maximo);
    await user.type(maximo, "0");
    await user.click(
      screen.getByRole("button", { name: "Guardar ajustes del NAS" }),
    );

    expect(apiMock.updateNasConfig).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/debe ser un entero entre 1 y 500/),
    ).toBeInTheDocument();
  });

  it("explica el error del backend sin romper el formulario", async () => {
    apiMock.updateNasConfig.mockRejectedValue(
      new ApiError(400, "Reglas: JSON inválido en la línea 2, columna 3"),
    );
    const user = userEvent.setup();
    renderSettings();

    await screen.findByDisplayValue("50");
    await user.click(
      screen.getByRole("button", { name: "Guardar ajustes del NAS" }),
    );

    expect(
      await screen.findByText(/Reglas: JSON inválido/),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("50")).toBeInTheDocument();
  });

  it("envía las reglas como JSON en texto", async () => {
    const conReglas = {
      ...CONFIG,
      classifier_rules: {
        default_folder: "Documentos",
        rules: [{ folder: "Fotos", extensions: [".jpg"] }],
      },
    };
    apiMock.nasConfig.mockResolvedValue(conReglas);
    apiMock.updateNasConfig.mockResolvedValue(conReglas);
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByText("Reglas de clasificación (JSON)"));
    const editor = screen.getByPlaceholderText('{"rules": [...]}');
    expect((editor as HTMLTextAreaElement).value).toContain('"Fotos"');

    await user.click(
      screen.getByRole("button", { name: "Guardar ajustes del NAS" }),
    );
    await waitFor(() =>
      expect(apiMock.updateNasConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          classifier_rules: expect.stringContaining('"Fotos"'),
        }),
        "csrf-9",
      ),
    );
  });
});
