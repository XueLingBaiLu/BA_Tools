using HarmonyLib;
using Il2CppBrokenArrow.Shared.Ecs;

namespace BaModProbe
{
    // Proven target/parameter shapes, copied from BARO's Patch_DbLoadCompiled /
    // Patch_DbLoadByJson / Patch_DbLoadScenario group (identical typeof() + Postfix shape):
    //
    //   [HarmonyPatch(typeof(DataBaseService), "LoadCompiled")]  Postfix(DataBaseService __instance)
    //   [HarmonyPatch(typeof(DataBaseService), "LoadByJson")]    Postfix(DataBaseService __instance)
    //   [HarmonyPatch(typeof(DataBaseService), "LoadScenario")]  Postfix(DataBaseService __instance)
    //   [HarmonyPatch(typeof(DataBaseService), "Load")]          Postfix(DataBaseService __instance)
    //
    // Why Load() is also hooked: per our own notes (kb/11 section 1), LoadCompiled() is driven by
    // ProfileDataBaseResolver.ApplyForScenario() -- it only runs on entering a scenario, so a
    // session that stops at the main menu would never produce a dump. Load() is what Initialize()
    // (startup) and ReloadByDefault() (return to menu) go through, so one launch is enough.
    //
    // Access flags MEASURED on the proxy assembly (not the dummy): Load() and LoadByJson() are
    // PUBLIC there; the dummy assembly reports them private. Il2CppInterop widens them. Either
    // way Harmony patches them.
    //
    // Type provenance (Mono.Cecil over the Il2CppInterop proxy assembly):
    //   Il2CppBrokenArrow.dll :: Il2CppBrokenArrow.Shared.Ecs.DataBaseService
    //       Void LoadCompiled(DataBaseCompiled, String)
    //       Void LoadByJson()
    //       Void LoadScenario(String)
    //       DataBaseSourceData RawAccess   (property, getter only)

    [HarmonyPatch(typeof(DataBaseService), "LoadCompiled")]
    internal static class Patch_LoadCompiled
    {
        private static void Postfix(DataBaseService __instance)
        {
            ProbeMod.OnDatabaseLoaded(__instance, "LoadCompiled");
        }
    }

    [HarmonyPatch(typeof(DataBaseService), "LoadByJson")]
    internal static class Patch_LoadByJson
    {
        private static void Postfix(DataBaseService __instance)
        {
            ProbeMod.OnDatabaseLoaded(__instance, "LoadByJson");
        }
    }

    [HarmonyPatch(typeof(DataBaseService), "LoadScenario")]
    internal static class Patch_LoadScenario
    {
        private static void Postfix(DataBaseService __instance)
        {
            ProbeMod.OnDatabaseLoaded(__instance, "LoadScenario");
        }
    }

    // Void Load() -- instance, no parameters. Fires from Initialize() at startup and from
    // ReloadByDefault() on every return to the main menu, so ProbeMod throttles the dump per hook
    // kind and DROPS (never rewrites) the previous journal; see OnDatabaseLoaded.
    [HarmonyPatch(typeof(DataBaseService), "Load")]
    internal static class Patch_Load
    {
        private static void Postfix(DataBaseService __instance)
        {
            ProbeMod.OnDatabaseLoaded(__instance, "Load");
        }
    }
}
