using System;
using System.Globalization;
using System.IO;
using System.Text;
using MelonLoader;
using MelonLoader.Utils;

namespace BaModProbe
{
    /// <summary>
    /// Every path the probe touches, rooted at
    ///     &lt;GameDir&gt;\UserData\BaModProbe\
    /// which is MelonEnvironment.UserDataDirectory -- the same anchor BARO uses
    /// (15 call sites of MelonEnvironment.UserDataDirectory in its decompiled source).
    /// Type provenance: MelonLoader.dll::MelonLoader.Utils.MelonEnvironment.UserDataDirectory
    /// </summary>
    internal static class ProbePaths
    {
        internal const string ModVersion = "0.1.0";
        internal const string FolderName = "BaModProbe";

        internal static string Root        { get { return Path.Combine(MelonEnvironment.UserDataDirectory, FolderName); } }
        internal static string DumpLatest  { get { return Path.Combine(Root, "dump_latest.json"); } }
        internal static string DumpsDir    { get { return Path.Combine(Root, "dumps"); } }
        internal static string OverrideFile{ get { return Path.Combine(Root, "override.json"); } }
        internal static string ColumnsFile { get { return Path.Combine(Root, "columns.json"); } }
        internal static string RestoreFlag { get { return Path.Combine(Root, "restore.flag"); } }
        internal static string DiagnoseFile{ get { return Path.Combine(Root, "diagnose_columns.txt"); } }
        internal static string RedumpFlag  { get { return Path.Combine(Root, "redump.flag"); } }
        internal static string LogFile     { get { return Path.Combine(Root, "probe.log"); } }

        internal static void EnsureDirs()
        {
            Directory.CreateDirectory(Root);
            Directory.CreateDirectory(DumpsDir);
        }

        internal static string Iso8601Utc()
        {
            return DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
        }

        internal static string FileStampUtc()
        {
            return DateTime.UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'", CultureInfo.InvariantCulture);
        }

        internal static DateTime OverrideWriteTimeUtc()
        {
            try { return File.Exists(OverrideFile) ? File.GetLastWriteTimeUtc(OverrideFile) : DateTime.MinValue; }
            catch { return DateTime.MinValue; }
        }

        /// <summary>Write text as UTF-8 without BOM (so downstream JSON parsers see plain bytes).</summary>
        internal static void WriteText(string path, string text)
        {
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            File.WriteAllText(path, text, new UTF8Encoding(false));
        }
    }

    internal static class ProbeLog
    {
        private static readonly object Gate = new object();

        internal static void Info(string message) { Emit("INFO ", message); }
        internal static void Warn(string message) { Emit("WARN ", message); }
        internal static void Error(string message, Exception ex)
        {
            Emit("ERROR", message + (ex == null ? "" : " :: " + ex.GetType().Name + ": " + ex.Message));
            if (ex != null) Emit("ERROR", ex.ToString());
        }

        private static void Emit(string level, string message)
        {
            string line = ProbePaths.Iso8601Utc() + " [" + level + "] " + message;
            try { MelonLogger.Msg("[BaModProbe] " + level.Trim() + " " + message); }
            catch { /* logger not ready during very early init */ }
            try
            {
                lock (Gate)
                {
                    ProbePaths.EnsureDirs();
                    File.AppendAllText(ProbePaths.LogFile, line + Environment.NewLine, new UTF8Encoding(false));
                }
            }
            catch { /* never let logging break the game */ }
        }
    }
}
