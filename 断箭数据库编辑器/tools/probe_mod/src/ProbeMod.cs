using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using Il2CppBrokenArrow.Shared.Ecs;
using MelonLoader;

// MelonLoader.dll :: MelonLoader.MelonInfoAttribute(Type, String name, String version, String author, String downloadLink)
// -- there is no 4-argument overload, so the download link is passed explicitly as null.
[assembly: MelonInfo(typeof(BaModProbe.ProbeMod), "BaModProbe", "0.1.0", "BA_Mod_Maker_2.0", null)]

namespace BaModProbe
{
    /// <summary>
    /// Debug probe for Broken Arrow. Two jobs:
    ///   1. dump the live database behind db.RawAccess into the shared JSON contract
    ///   2. apply a sparse per-row override, with a one-shot static Restore()
    ///
    /// This is a debugging tool. It is not part of the shipped product.
    /// </summary>
    public sealed class ProbeMod : MelonMod
    {
        private static object _lastSource;
        private static string _lastHook = "";
        private static string _lastSourceId = "";
        private static DateTime _lastOverrideStamp = DateTime.MinValue;
        private static DateTime _nextPollUtc = DateTime.MinValue;
        private static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(2);

        /// <summary>
        /// Hook kinds already dumped in this session. Load() fires on startup AND on every return
        /// to the main menu, and LoadCompiled() fires on every scenario entry, so without this the
        /// same dump would be taken over and over. A genuinely different load (a different hook)
        /// still gets its own dump. redump.flag forces a fresh one on demand.
        /// </summary>
        private static readonly HashSet<string> _dumpedHooks = new HashSet<string>(StringComparer.Ordinal);

        public override void OnInitializeMelon()
        {
            try
            {
                ProbePaths.EnsureDirs();
                ProbeLog.Info("BaModProbe " + ProbePaths.ModVersion + " initialising");
                ProbeLog.Info("UserData root = " + ProbePaths.Root);

                Contract.LoadColumns();

                HarmonyInstance.PatchAll(Assembly.GetExecutingAssembly());
                ProbeLog.Info("patched (Postfix): DataBaseService.Load / LoadCompiled / LoadByJson / LoadScenario");

                _lastOverrideStamp = ProbePaths.OverrideWriteTimeUtc();
                ProbeLog.Info("watching " + ProbePaths.OverrideFile);
                ProbeLog.Info("control files: restore.flag (one-shot Restore), redump.flag (re-dump now)");
            }
            catch (Exception ex)
            {
                ProbeLog.Error("OnInitializeMelon failed -- the probe is inert", ex);
            }
        }

        public override void OnUpdate()
        {
            try
            {
                if (DateTime.UtcNow < _nextPollUtc) return;
                _nextPollUtc = DateTime.UtcNow + PollInterval;
                PollControlFiles();
            }
            catch (Exception ex)
            {
                ProbeLog.Error("OnUpdate failed", ex);
            }
        }

        /// <summary>
        /// The one-shot undo required by task 2: put every cell this probe wrote back to the
        /// value it had before. Static so it can be called from anywhere (and from a patch).
        /// </summary>
        public static int Restore()
        {
            return WriteJournal.Restore();
        }

        /// <summary>Called from the three DataBaseService Postfixes.</summary>
        internal static void OnDatabaseLoaded(DataBaseService instance, string hookName)
        {
            try
            {
                if (instance == null) { ProbeLog.Warn(hookName + ": __instance is null"); return; }

                object source = null;
                try { source = instance.RawAccess; }
                catch (Exception ex) { ProbeLog.Error(hookName + ": reading RawAccess threw", ex); }

                if (source == null) { ProbeLog.Warn(hookName + ": RawAccess is null -- database not available"); return; }

                string sourceId = ReadSourceId(instance);
                _lastSource = source;
                _lastHook = hookName;
                _lastSourceId = sourceId;
                ProbeLog.Info(hookName + ": RawAccess acquired (source_id='" + sourceId + "')");

                // A load rebuilds the source object, so anything this probe wrote earlier is gone
                // and the row proxies in the journal may have been freed. Writing through a freed
                // Il2Cpp object is a NATIVE access violation rather than a managed exception, so on
                // the reload path we DROP the journal instead of restoring it. Restore() remains
                // for the live-database paths (restore.flag, override.json edited mid-session).
                int dropped = WriteJournal.Discard();
                if (dropped > 0)
                {
                    ProbeLog.Info(hookName + ": discarded journal of " + dropped
                        + " cell(s) from the previous load (values were rebuilt by the game)");
                }

                // Job 1 -- read-only dump, throttled per hook kind.
                if (_dumpedHooks.Add(hookName))
                {
                    DbDump.Run(source, hookName, sourceId);
                }
                else
                {
                    ProbeLog.Info(hookName + ": dump already taken for this hook this session"
                        + " -- drop redump.flag into UserData\\BaModProbe to force a fresh one");
                }

                // Job 2 -- sparse override. Re-applied on EVERY load, because a reload wipes it.
                DbOverride.Report rep = DbOverride.Apply(source, hookName);
                if (rep.CellsWritten > 0 || rep.Problems.Count > 0)
                {
                    ProbeLog.Info(hookName + ": override wrote " + rep.CellsWritten + " cell(s); "
                        + "ProbeMod.Restore() undoes them");
                }
            }
            catch (Exception ex)
            {
                ProbeLog.Error("OnDatabaseLoaded(" + hookName + ") failed", ex);
            }
        }

        /// <summary>DataBaseService.CurrentSourceId, read reflectively so we never guess its CLR shape.</summary>
        private static string ReadSourceId(DataBaseService instance)
        {
            try
            {
                PropertyInfo p = typeof(DataBaseService).GetProperty("CurrentSourceId", RowAccess.PubInst);
                if (p == null) return "";
                object v = p.GetValue(instance);
                return v == null ? "" : Convert.ToString(v, CultureInfo.InvariantCulture);
            }
            catch { return ""; }
        }

        /// <summary>
        /// File-driven control channel -- no keyboard or Unity input API needed, so the probe
        /// can be driven while the game is running:
        ///   restore.flag  delete + Restore()        (one-shot, removed after use)
        ///   redump.flag   delete + dump again now   (one-shot, removed after use)
        ///   override.json edited -> Restore() then re-apply
        /// </summary>
        private static void PollControlFiles()
        {
            if (File.Exists(ProbePaths.RestoreFlag))
            {
                TryDelete(ProbePaths.RestoreFlag);
                int n = WriteJournal.Restore();
                ProbeLog.Info("restore.flag seen -> Restore() put back " + n + " cell(s)");
            }

            if (File.Exists(ProbePaths.RedumpFlag))
            {
                TryDelete(ProbePaths.RedumpFlag);
                if (_lastSource == null) ProbeLog.Warn("redump.flag seen but no database has been loaded yet");
                else DbDump.Run(_lastSource, _lastHook + "+redump", _lastSourceId);
            }

            DateTime stamp = ProbePaths.OverrideWriteTimeUtc();
            if (stamp != _lastOverrideStamp)
            {
                _lastOverrideStamp = stamp;
                if (_lastSource == null)
                {
                    ProbeLog.Warn("override.json changed but no database has been loaded yet");
                    return;
                }
                int n = WriteJournal.Restore();
                DbOverride.Report rep = DbOverride.Apply(_lastSource, "override.json changed");
                ProbeLog.Info("override.json changed (restored " + n + " cell(s) first); " + rep.Text());
            }
        }

        private static void TryDelete(string path)
        {
            try { File.Delete(path); }
            catch (Exception ex) { ProbeLog.Warn("could not delete " + path + ": " + ex.Message); }
        }
    }
}
