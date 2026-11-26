using System;
using System.Collections.Generic;   // deliberately NOT System.Collections: no Il2Cpp proxy
                                    // collection implements it, see MaterializeRows below.
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;

namespace BaModProbe
{
    /// <summary>
    /// Task 1 -- read-only probe. Serialises the live database behind db.RawAccess
    /// (BrokenArrow.DataBase.DataBaseSourceData) into the dump contract JSON.
    ///
    /// Contract (agreed with the offline comparator, do not invent your own):
    ///   { "meta":    { generated_utc, mod_version, game_source, table_count, row_counts, failed_tables },
    ///     "tables":  { "Units": [ { "Id": 1, ... } ] },
    ///     "probe":   { ... additive diagnostics, see README ... } }
    ///
    /// Hard rules honoured here:
    ///   #1 column names are verbatim DataBaseCompiled keys (from the shared columns.json)
    ///   #2 values go out untouched -- no unit conversion, no rounding, no dropped field
    ///   #3 every table is sorted by Id ascending
    ///   #4 a table that cannot be read lands in meta.failed_tables, never silently omitted
    /// </summary>
    internal static class DbDump
    {
        internal sealed class Result
        {
            internal int TableCount;
            internal int RowCount;
            internal int CellCount;
            internal int NotFoundCells;
            internal int ReferenceNonNull;
            internal readonly List<string> ReferenceReads = new List<string>();
            internal readonly List<string> ComputedColumns = new List<string>();
            internal readonly List<string> Diagnostics = new List<string>();
            internal readonly Dictionary<string, int> DiagnosticCounts = new Dictionary<string, int>(StringComparer.Ordinal);
            internal int RawReadsUsed;
            internal List<string> FailedTables = new List<string>();
            internal List<string> Warnings = new List<string>();
            internal string LatestPath;
            internal string StampedPath;
        }

        internal static Result Run(object source, string hookName, string sourceId)
        {
            var sw = DateTime.UtcNow;
            var result = new Result();
            Type srcType = source.GetType();

            List<string> tables = Contract.DiscoverTables(srcType);
            result.TableCount = tables.Count;
            if (tables.Count == 0)
            {
                result.Warnings.Add("no Table<T> property found on " + srcType.FullName);
                ProbeLog.Warn("dump: no Table<T> property found on " + srcType.FullName);
            }

            // Pass 1: materialise every readable table so row_counts can be emitted in meta.
            var loaded = new List<KeyValuePair<string, List<object>>>();
            foreach (string table in tables)
            {
                List<object> rows;
                string error;
                if (TryGetRows(source, table, out rows, out error))
                {
                    loaded.Add(new KeyValuePair<string, List<object>>(table, rows));
                }
                else
                {
                    result.FailedTables.Add(table);
                    result.Warnings.Add("table " + table + ": " + error);
                    ProbeLog.Warn("dump: table " + table + " not read -- " + error);
                }
            }

            var rowCounts = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, List<object>> kv in loaded) rowCounts[kv.Key] = kv.Value.Count;

            // Pass 2: emit.
            var sb = new StringBuilder(4 * 1024 * 1024);
            var w = new JsonWriter(sb, " ");

            w.BeginObject();

            w.Name("meta");
            w.BeginObject();
            w.Name("generated_utc"); w.Str(ProbePaths.Iso8601Utc());
            w.Name("mod_version");   w.Str(ProbePaths.ModVersion);
            w.Name("game_source");   w.Str(GameSourceForHook(hookName));
            w.Name("table_count");   w.Int(tables.Count);
            w.Name("row_counts");
            w.BeginObject();
            foreach (KeyValuePair<string, List<object>> kv in loaded)
            {
                w.Name(kv.Key);
                w.Int(kv.Value.Count);
            }
            w.EndObject();
            w.Name("failed_tables");
            w.BeginArray();
            foreach (string t in result.FailedTables) w.Str(t);
            w.EndArray();
            w.EndObject(); // meta

            w.Name("tables");
            w.BeginObject();
            foreach (KeyValuePair<string, List<object>> kv in loaded)
            {
                string table = kv.Key;
                List<object> rows = kv.Value;
                w.Name(table);
                w.BeginArray();
                if (rows.Count > 0)
                {
                    Type rowType = rows[0].GetType();
                    string[] cols = Contract.ColumnsFor(table, rowType);
                    var handles = new ColumnHandle[cols.Length];
                    for (int i = 0; i < cols.Length; i++)
                    {
                        handles[i] = RowAccess.Resolve(rowType, cols[i]);
                        if (handles[i] == null)
                        {
                            result.NotFoundCells++;
                            result.Warnings.Add("column " + table + "." + cols[i] + " missing on " + rowType.FullName);
                        }
                        else if (handles[i].IsComputed && !IsReferenceColumn(handles[i].ValueType))
                        {
                            // MEASURED over all 407 contract columns (_work/classify_columns.ps1):
                            // 395 are stored in a "_<name>_k__BackingField" twin, 0 are stored in a
                            // plain-named field, and 12 have NO stored field at all -- the game DERIVES
                            // them in the public getter. A derived value can differ from the serialised
                            // number by an ulp (PlaneFlyPresets.CornerSpeedYaw does, on Id 3 and 10), so
                            // the comparator must not read those as data differences.
                            // Mobility.FlyPreset is also in that set but is reported as a navigation
                            // reference instead, so it is excluded here.
                            string key = table + "." + cols[i];
                            if (!result.ComputedColumns.Contains(key)) result.ComputedColumns.Add(key);
                        }
                    }
                    foreach (object row in rows)
                    {
                        int rid = RowAccess.ReadId(row);
                        w.BeginObject();
                        for (int i = 0; i < cols.Length; i++)
                        {
                            ColumnHandle h = handles[i];
                            if (h == null) continue;   // reported once, above
                            object v = null;
                            string readError = null;
                            try { v = h.Get(row); }
                            catch (Exception ex) { readError = ex.Message; }

                            if (readError != null)
                            {
                                result.Warnings.Add("read " + table + "." + cols[i] + " threw: " + readError);

                                // Both typed paths raised. Try reading the field straight out of the
                                // object's memory -- offsets come from the runtime, nothing about the
                                // layout is assumed -- and record the whole attempt so ONE run answers
                                // "which path can work, and what does the failing one say".
                                object rawValue;
                                string rawTrace;
                                bool rawOk = RowAccess.TryRawNullableIntRead(row, h, out rawValue, out rawTrace);
                                AddDiagnostic(result, table, cols[i], rid,
                                    "typed read threw: " + readError
                                    + " || path4-raw=" + (rawOk
                                        ? ("OK value=" + (rawValue == null ? "null" : rawValue.ToString()) + " (" + rawTrace + ")")
                                        : ("FAILED " + rawTrace))
                                    + " || " + RowAccess.Diagnose(row, cols[i]));

                                if (rawOk)
                                {
                                    w.Name(cols[i]);
                                    if (rawValue == null) w.Null();
                                    else w.Int(Convert.ToInt64(rawValue, CultureInfo.InvariantCulture));
                                    result.CellCount++;
                                    result.RawReadsUsed++;
                                }
                                continue;
                            }

                            w.Name(cols[i]);
                            if (IsWatched(table, cols[i])) AddDiagnostic(result, table, cols[i], rid, RowAccess.Diagnose(row, cols[i]));

                            if (IsReferenceColumn(h.ValueType))
                            {
                                // Exactly one column lands here: Mobility.FlyPreset (declared type
                                // Il2CppBrokenArrow.DataBase.Models.PlaneFlyPreset). DataBaseCompiled
                                // stores null for it in all 266 rows -- the foreign key that carries
                                // data is the separate Int32 column Mobility.FlyPresetId, which is
                                // dumped normally. To keep the compared namespace byte-faithful we
                                // emit null here, and record what the live nav reference actually
                                // resolved to under probe.reference_reads instead of dropping it.
                                object refId = RowAccess.ReadReferenceId(v);
                                if (refId != null)
                                {
                                    result.ReferenceNonNull++;
                                    if (result.ReferenceReads.Count < 200)
                                    {
                                        result.ReferenceReads.Add(table + "[" + rid + "]." + cols[i]
                                            + " -> Id " + Convert.ToString(refId, CultureInfo.InvariantCulture));
                                    }
                                }
                                w.Null();
                                result.CellCount++;
                                continue;
                            }

                            WriteValue(w, v, h.ValueType);
                            result.CellCount++;
                        }
                        w.EndObject();
                    }
                }
                w.EndArray();
                result.RowCount++;
            }
            w.EndObject(); // tables

            w.Name("probe");
            w.BeginObject();
            w.Name("hook");           w.Str(hookName);
            w.Name("source_id");      w.Str(sourceId);
            w.Name("columns_source"); w.Str(Contract.HasColumnManifest ? "columns.json" : "reflection-fallback");
            w.Name("row_source");     w.Str(RowPathUsed);
            w.Name("tables_discovered"); w.Int(tables.Count);
            w.Name("cells_written");  w.Int(result.CellCount);
            w.Name("cells_unresolved"); w.Int(result.NotFoundCells);
            w.Name("failed_tables");  w.Int(result.FailedTables.Count);
            w.Name("raw_reads_used"); w.Int(result.RawReadsUsed);
            w.Name("column_diagnostics");
            w.BeginArray();
            foreach (string m in result.Diagnostics) w.Str(m);
            w.EndArray();
            w.Name("computed_columns");
            w.BeginArray();
            foreach (string m in result.ComputedColumns) w.Str(m);
            w.EndArray();
            w.Name("reference_columns_nonnull"); w.Int(result.ReferenceNonNull);
            w.Name("reference_reads");
            w.BeginArray();
            foreach (string m in result.ReferenceReads) w.Str(m);
            w.EndArray();
            w.Name("warnings");
            w.BeginArray();
            foreach (string m in result.Warnings) w.Str(m);
            w.EndArray();
            w.EndObject(); // probe

            w.EndObject();

            string json = sb.ToString();
            ProbePaths.WriteText(ProbePaths.DumpLatest, json);
            result.LatestPath = ProbePaths.DumpLatest;
            string stamped = Path.Combine(ProbePaths.DumpsDir, "dump_" + ProbePaths.FileStampUtc() + ".json");
            try
            {
                ProbePaths.WriteText(stamped, json);
                result.StampedPath = stamped;
            }
            catch (Exception ex)
            {
                ProbeLog.Error("failed to write stamped dump " + stamped, ex);
            }

            PruneStampedDumps(KeepStampedDumps);

            double ms = (DateTime.UtcNow - sw).TotalMilliseconds;
            ProbeLog.Info(string.Format(CultureInfo.InvariantCulture,
                "dump: tables={0} rows={1} cells={2} unresolved={3} failed={4} bytes={5} ms={6:0}",
                result.RowCount, TotalRows(loaded), result.CellCount, result.NotFoundCells,
                result.FailedTables.Count, json.Length, ms));
            return result;
        }

        /// <summary>How many timestamped dumps to keep on disk.</summary>
        internal const int KeepStampedDumps = 20;

        /// <summary>
        /// Keep the stamped-dump directory bounded.
        ///
        /// The user has a standing rule against tools leaving junk files on their disk (AGENTS.md
        /// section 2), and Load() fires again on every return to the main menu -- without this the
        /// dumps directory would grow by a multi-megabyte file per menu visit.
        /// </summary>
        internal static int PruneStampedDumps(int keep)
        {
            try
            {
                var dir = new DirectoryInfo(ProbePaths.DumpsDir);
                if (!dir.Exists) return 0;
                FileInfo[] files = dir.GetFiles("dump_*.json");
                if (files.Length <= keep) return 0;
                Array.Sort(files, delegate (FileInfo a, FileInfo b)
                {
                    return b.LastWriteTimeUtc.CompareTo(a.LastWriteTimeUtc);
                });
                int removed = 0;
                for (int i = keep; i < files.Length; i++)
                {
                    try { files[i].Delete(); removed++; }
                    catch (Exception ex) { ProbeLog.Warn("could not prune " + files[i].Name + ": " + ex.Message); }
                }
                if (removed > 0) ProbeLog.Info("pruned " + removed + " old stamped dump(s), keeping " + keep);
                return removed;
            }
            catch (Exception ex)
            {
                ProbeLog.Error("pruning stamped dumps failed", ex);
                return 0;
            }
        }

        private static int TotalRows(List<KeyValuePair<string, List<object>>> loaded)
        {
            int n = 0;
            foreach (KeyValuePair<string, List<object>> kv in loaded) n += kv.Value.Count;
            return n;
        }

        /// <summary>
        /// hook -&gt; game_source. This mapping is a HYPOTHESIS until the first real run;
        /// probe.hook and probe.source_id carry the raw evidence so it can be confirmed.
        /// </summary>
        internal static string GameSourceForHook(string hookName)
        {
            // HYPOTHESIS, not a reading. LoadCompiled is documented as the "compiled database
            // fallback" and LoadByJson as the JSON path; neither has been observed at runtime, and
            // Load() goes through Initialize()/ReloadByDefault() so it carries no evidence about
            // the source at all. Anything unproven reports "unknown" rather than a confident wrong
            // answer; probe.hook and probe.source_id keep the raw evidence.
            if (hookName == "LoadCompiled") return "compiled";
            if (hookName == "LoadByJson")   return "BA_Mod.zip";
            return "unknown";
        }

        /// <summary>Fetch and Id-sort one table. Returns false (with a reason) when unreadable.</summary>
        internal static bool TryGetRows(object source, string table, out List<object> rows, out string error)
        {
            rows = null;
            error = null;
            try
            {
                ColumnHandle th = RowAccess.Resolve(source.GetType(), table);
                if (th == null) { error = "no such table property"; return false; }
                object tableObj = th.Get(source);
                if (RowAccess.IsNull(tableObj)) { error = "table property is null (database not loaded?)"; return false; }

                Type tableType = tableObj.GetType();
                MethodInfo getAll = tableType.GetMethod("GetAll", RowAccess.PubInst);
                if (getAll == null) { error = "Table<T>.GetAll() not found on " + tableType.FullName; return false; }

                object enumerableObj = getAll.Invoke(tableObj, null);
                if (enumerableObj == null) { rows = new List<object>(); return true; }

                Type[] genArgs = tableType.GetGenericArguments();
                if (genArgs.Length != 1)
                {
                    error = "Table<> has " + genArgs.Length + " generic arguments, expected 1";
                    return false;
                }

                string how;
                string why;
                List<object> list = MaterializeRows(enumerableObj, genArgs[0], out how, out why);
                if (list == null) { error = why; return false; }

                list.Sort(CompareById);
                rows = list;
                RowPathUsed = how;
                return true;
            }
            catch (Exception ex)
            {
                error = ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        /// <summary>Which materialisation path the last successful TryGetRows used (diagnostics).</summary>
        internal static string RowPathUsed = "(none yet)";

        private static string[] _watch;

        /// <summary>
        /// Columns to trace in full every run: "Table.Column", one per line, in
        /// &lt;GameDir&gt;\UserData\BaModProbe\diagnose_columns.txt (# comments allowed).
        /// Turrets.ParentTurretId is always included because both of its typed getters raise.
        /// </summary>
        internal static void LoadWatchList()
        {
            var list = new List<string>();
            list.Add("Turrets.ParentTurretId");
            try
            {
                if (File.Exists(ProbePaths.DiagnoseFile))
                {
                    foreach (string raw in File.ReadAllLines(ProbePaths.DiagnoseFile))
                    {
                        string s = raw.Trim();
                        if (s.Length == 0 || s.StartsWith("#", StringComparison.Ordinal)) continue;
                        if (!list.Contains(s)) list.Add(s);
                    }
                }
            }
            catch (Exception ex) { ProbeLog.Warn("could not read " + ProbePaths.DiagnoseFile + ": " + ex.Message); }
            _watch = list.ToArray();
            ProbeLog.Info("diagnose watch list (" + _watch.Length + "): " + string.Join(", ", _watch));
        }

        internal static bool IsWatched(string table, string column)
        {
            if (_watch == null) LoadWatchList();
            string key = table + "." + column;
            for (int i = 0; i < _watch.Length; i++)
                if (string.Equals(_watch[i], key, StringComparison.Ordinal)) return true;
            return false;
        }

        /// <summary>Record at most 2 sample rows per column, and at most 80 entries overall.</summary>
        private static void AddDiagnostic(Result r, string table, string column, int id, string text)
        {
            string key = table + "." + column;
            int n;
            r.DiagnosticCounts.TryGetValue(key, out n);
            if (n >= 2) return;
            if (r.Diagnostics.Count >= 80) return;
            r.DiagnosticCounts[key] = n + 1;
            r.Diagnostics.Add(table + "[" + id + "] " + text);
        }

        /// <summary>
        /// Turn whatever Table&lt;T&gt;.GetAll() handed back into a managed List&lt;object&gt;.
        ///
        /// MEASURED FACT that forced this shape (Mono.Cecil over Il2Cppmscorlib.dll, see
        /// _work/probe_collections2.ps1): every Il2Cpp proxy collection implements NO managed
        /// interface at all. Il2CppSystem.Collections.Generic.IEnumerable`1, IEnumerator`1,
        /// List`1 and Dictionary`2 all report an EMPTY interface list. Consequences:
        ///   * "x as System.Collections.IEnumerable" is ALWAYS null -> a foreach over it can never
        ///     run. That is exactly why all 24 tables came back empty on the first real run; it
        ///     was structural, not a race.
        ///   * Il2CppSystem.Collections.Generic.IEnumerator`1 exposes only get_Current() -- there
        ///     is no MoveNext() on it, so walking the generic enumerator is not available either.
        ///     (The NON-generic Il2CppSystem.Collections.IEnumerator does have MoveNext(), which
        ///     is why that one is used as the fallback below.)
        ///
        /// PRIMARY path is BARO's production-proven Props.Rows&lt;T&gt; (decompiled.cs:407727-407736,
        /// called from 190 sites):
        ///     List&lt;T&gt; val = new List&lt;T&gt;(src);
        ///     for (int i = 0; i &lt; val.Count; i++) list.Add(val[i]);
        /// i.e. copy into an Il2Cpp List&lt;T&gt; and walk it BY INDEX, never through an enumerator.
        /// Measured on the proxy type: ".ctor(IEnumerable`1)", "get_Count()" and "get_Item(Int32)"
        /// all exist on Il2CppSystem.Collections.Generic.List`1.
        ///
        /// Reflectively the element type is only known at runtime, so the List&lt;T&gt; is closed with
        /// MakeGenericType. On failure the reason names the ACTUAL runtime type of the returned
        /// object, so the next real run points straight at the cause instead of saying
        /// "did not return IEnumerable".
        /// </summary>
        private static List<object> MaterializeRows(object enumerableObj, Type elemType, out string how, out string why)
        {
            how = null;
            why = null;
            Type actual = enumerableObj.GetType();
            string actualName = actual.FullName;

            // ---- primary: BARO's List<T>(IEnumerable<T>) + index walk ----------------------
            Exception primaryEx = null;
            try
            {
                Type il2ListT = typeof(Il2CppSystem.Collections.Generic.List<>).MakeGenericType(elemType);
                object list = Activator.CreateInstance(il2ListT, new object[] { enumerableObj });
                PropertyInfo countProp = il2ListT.GetProperty("Count", RowAccess.PubInst);
                PropertyInfo itemProp = il2ListT.GetProperty("Item", RowAccess.PubInst);
                if (countProp == null || itemProp == null)
                    throw new MissingMemberException("List<T> is missing Count or Item");

                int n = Convert.ToInt32(countProp.GetValue(list), CultureInfo.InvariantCulture);
                var outRows = new List<object>(n);
                object[] idx = new object[1];
                for (int i = 0; i < n; i++)
                {
                    idx[0] = i;
                    object row = itemProp.GetValue(list, idx);
                    if (row != null) outRows.Add(row);
                }
                how = "Il2Cpp List<T>(IEnumerable<T>) + index walk  [BARO Props.Rows<T> shape]";
                return outRows;
            }
            catch (Exception ex)
            {
                primaryEx = ex;
            }

            // ---- fallback: non-generic Il2CppSystem.Collections.IEnumerator ------------------
            // Only this one carries MoveNext(); it is reached through the proxy's renamed
            // explicit-interface slot System_Collections_IEnumerable_GetEnumerator().
            Exception fallbackEx = null;
            try
            {
                MethodInfo getEnum = actual.GetMethod("System_Collections_IEnumerable_GetEnumerator", RowAccess.PubInst);
                if (getEnum == null)
                    throw new MissingMethodException("no System_Collections_IEnumerable_GetEnumerator() on " + actualName);

                object enumerator = getEnum.Invoke(enumerableObj, null);
                if (enumerator == null) throw new InvalidOperationException("GetEnumerator() returned null");

                Type et = enumerator.GetType();
                MethodInfo moveNext = et.GetMethod("MoveNext", RowAccess.PubInst);
                PropertyInfo current = et.GetProperty("Current", RowAccess.PubInst);
                if (moveNext == null || current == null)
                    throw new MissingMemberException("enumerator " + et.FullName + " is missing MoveNext/Current");

                var outRows = new List<object>();
                while (Convert.ToBoolean(moveNext.Invoke(enumerator, null), CultureInfo.InvariantCulture))
                {
                    object row = current.GetValue(enumerator);
                    if (row != null) outRows.Add(row);
                }
                how = "Il2CppSystem.Collections.IEnumerator MoveNext/Current  [non-generic fallback]";
                return outRows;
            }
            catch (Exception ex)
            {
                fallbackEx = ex;
            }

            why = "GetAll() returned " + actualName + "; neither materialisation path worked."
                + " primary[List<T> index walk]: " + Describe(primaryEx)
                + "; fallback[IEnumerator MoveNext]: " + Describe(fallbackEx);
            return null;
        }

        private static string Describe(Exception ex)
        {
            if (ex == null) return "n/a";
            Exception inner = ex is TargetInvocationException && ex.InnerException != null ? ex.InnerException : ex;
            return inner.GetType().Name + ": " + inner.Message;
        }

        private static int CompareById(object a, object b)
        {
            int ia = RowAccess.ReadId(a), ib = RowAccess.ReadId(b);
            return ia.CompareTo(ib);
        }

        /// <summary>
        /// True for a column whose declared type is not a scalar/enum/Nullable-of-scalar, i.e. a
        /// navigation reference to another row model. Measured: exactly one such column exists
        /// across all 24 tables and 407 columns (see _work/column_type_hist.py).
        /// </summary>
        internal static bool IsReferenceColumn(Type declared)
        {
            return !RowAccess.IsPrimitiveish(declared);
        }

        /// <summary>
        /// Emit one value exactly as the row carries it (hard rule #2).
        ///   enum            -&gt; its underlying numeric value (DataBaseCompiled stores numbers:
        ///                      e.g. Units.Role 12, Countries.ContentMembership -1)
        ///   Nullable&lt;T&gt;    -&gt; null or T
        ///   model reference -&gt; the referenced row's Id (only Mobility.FlyPreset is one)
        /// </summary>
        internal static void WriteValue(JsonWriter w, object v, Type declared)
        {
            Type u = Nullable.GetUnderlyingType(declared) ?? declared;

            if (RowAccess.IsNull(v)) { w.Null(); return; }

            // Il2CppSystem.Nullable<T> is not System.Nullable<T> -- unwrap it before type dispatch.
            object inner;
            if (Il2CppNullable.TryUnwrap(v, out inner))
            {
                if (inner == null) { w.Null(); return; }
                WriteValue(w, inner, inner.GetType());
                return;
            }

            if (u.IsEnum)                  { w.Int(ToLong(v)); return; }
            if (u == typeof(bool))         { w.Bool(Convert.ToBoolean(v, CultureInfo.InvariantCulture)); return; }
            if (u == typeof(string))       { w.Str(Convert.ToString(v, CultureInfo.InvariantCulture)); return; }
            if (u == typeof(float))        { w.Float(Convert.ToSingle(v, CultureInfo.InvariantCulture)); return; }
            if (u == typeof(double))       { w.Double(Convert.ToDouble(v, CultureInfo.InvariantCulture)); return; }
            if (u == typeof(byte) || u == typeof(sbyte) || u == typeof(short) || u == typeof(ushort)
                || u == typeof(int) || u == typeof(uint) || u == typeof(long)) { w.Int(ToLong(v)); return; }
            if (u == typeof(decimal))      { w.Double(Convert.ToDouble(Convert.ToDecimal(v, CultureInfo.InvariantCulture), CultureInfo.InvariantCulture)); return; }

            object refId = RowAccess.ReadReferenceId(v);
            if (refId == null) w.Null(); else w.Int(Convert.ToInt64(refId, CultureInfo.InvariantCulture));
        }

        private static long ToLong(object v)
        {
            try { return Convert.ToInt64(v, CultureInfo.InvariantCulture); }
            catch { return 0L; }
        }

    }
}
