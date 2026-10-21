import { describe, expect, it } from "vitest";
import { mapStored } from "./Chat";
import type { StoredMessage } from "../types";

/**
 * El historial se reconstruye desde las filas crudas de SQLite. Antes se
 * usaba el formato del LLM, que no trae `id`: todas las burbujas acababan con
 * la misma clave de React (`h-undefined`) y las herramientas se descartaban.
 */

function fila(overrides: Partial<StoredMessage>): StoredMessage {
  return {
    id: 1,
    role: "user",
    content: "",
    tool_calls: null,
    tool_call_id: null,
    name: null,
    created_at: "2026-10-21T10:00:00+00:00",
    ...overrides,
  };
}

describe("mapStored", () => {
  it("usa ids únicos y conserva la fecha", () => {
    const salida = mapStored([
      fila({ id: 1, role: "user", content: "hola" }),
      fila({ id: 2, role: "assistant", content: "buenas" }),
    ]);

    expect(salida.map((m) => m.id)).toEqual(["h-1", "h-2"]);
    expect(salida[0].at).toBe("2026-10-21T10:00:00+00:00");
    expect(salida[1].content).toBe("buenas");
  });

  it("reagrupa la llamada y su resultado como una sola actividad", () => {
    const salida = mapStored([
      fila({ id: 1, role: "user", content: "¿cpu?" }),
      fila({
        id: 2,
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "call_1",
            type: "function",
            function: { name: "system_stats", arguments: '{"detalle":true}' },
          },
        ],
      }),
      fila({
        id: 3,
        role: "tool",
        content: '{"cpu":5}',
        tool_call_id: "call_1",
        name: "system_stats",
        tool_ok: true,
        tool_duration_ms: 42,
        tool_summary: "CPU 5%",
      }),
      fila({ id: 4, role: "assistant", content: "El CPU está al 5%." }),
    ]);

    // El assistant sin texto pero con tool_calls SÍ se conserva: lleva la
    // actividad. Su burbuja va vacía y no se pinta.
    expect(salida).toHaveLength(3);
    expect(salida[1].content).toBe("");
    expect(salida[1].tools).toHaveLength(1);
    expect(salida[1].tools?.[0]).toMatchObject({
      tool: "system_stats",
      ok: true,
      duration_ms: 42,
      summary: "CPU 5%",
    });
    expect(salida[1].tools?.[0].args).toContain('"detalle": true');
    expect(salida[2].content).toBe("El CPU está al 5%.");
  });

  it("marca el fallo aunque el resultado no traiga el resumen guardado", () => {
    const salida = mapStored([
      fila({
        id: 1,
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "call_9",
            function: { name: "docker_ps", arguments: "{}" },
          },
        ],
      }),
      fila({
        id: 2,
        role: "tool",
        content: '{"error":"permiso denegado"}',
        tool_call_id: "call_9",
        name: "docker_ps",
      }),
    ]);

    const paso = salida[0].tools?.[0];
    expect(paso?.ok).toBe(false);
    expect(paso?.summary).toBe("permiso denegado");
    // Los argumentos vacíos no se muestran como bloque de parámetros.
    expect(paso?.args).toBeUndefined();
  });

  it("ignora tool huérfanas y mensajes no visibles", () => {
    const salida = mapStored([
      fila({ id: 1, role: "tool", content: "{}", tool_call_id: "fantasma" }),
      fila({ id: 2, role: "system", content: "eres jarvis" }),
      fila({ id: 3, role: "assistant", content: "   " }),
      fila({ id: 4, role: "user", content: "hola" }),
    ]);

    expect(salida).toHaveLength(1);
    expect(salida[0]).toMatchObject({ id: "h-4", role: "user", content: "hola" });
  });
});
