using System;
using System.Globalization;
using System.Reflection;

namespace BaModProbe
{
    /// <summary>
    /// Unwrapping for Il2CppInterop's Nullable&lt;T&gt; proxy.
    ///
    /// MEASURED (Mono.Cecil over Il2Cppmscorlib.dll, _work/probe_nullable.ps1): Il2CppInterop models
    /// Il2Cpp's Nullable&lt;T&gt; as a CLASS named Il2CppSystem.Nullable\`1 whose base is
    /// Il2CppSystem.ValueType -- NOT as System.Nullable&lt;T&gt;. So System.Nullable.GetUnderlyingType
    /// returns null for it, the value fell through to the reference branch of DbDump.WriteValue, and
    /// Turrets.ParentTurretId was written as null even on the 86 rows that DO carry a value.
    ///
    /// The proxy exposes a field-backed "hasValue"/"value" pair that reads the boxed struct directly,
    /// next to the runtime-invoked "HasValue"/"Value". The lower-case pair is preferred: it cannot
    /// run game code and cannot throw.
    ///
    /// This file is deliberately dependency-free (System only) so tests/JsonTests can exercise it
    /// offline against a stand-in type -- see tests/JsonTests/FakeNullable.cs.
    /// </summary>
    internal static class Il2CppNullable
    {
        internal const string ProxyNamespace = "Il2CppSystem";
        internal const string ProxyNamePrefix = "Nullable";

        private const BindingFlags PubInst = BindingFlags.Public | BindingFlags.Instance;
        private const BindingFlags AnyInst = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

        /// <summary>
        /// True when <paramref name="t"/> is the Il2CppInterop proxy for Nullable&lt;T&gt;.
        ///
        /// This matters beyond unwrapping: RowAccess.IsPrimitiveish() decides whether a column is
        /// a scalar or a "navigation reference". Il2CppSystem.Nullable&lt;T&gt; is neither
        /// System.Nullable nor a primitive, so without this it was classified as a REFERENCE --
        /// and DbDump's row loop short-circuits reference columns to a literal null AFTER the read
        /// succeeds, so WriteValue's unwrap was never reached. That is why Turrets.ParentTurretId
        /// still produced 90 nulls on the run after the unwrap was added.
        /// </summary>
        internal static bool IsProxyNullable(Type t)
        {
            return UnderlyingOf(t) != null;
        }

        /// <summary>The T of an Il2CppSystem.Nullable&lt;T&gt;, or null when t is not one.</summary>
        internal static Type UnderlyingOf(Type t)
        {
            if (t == null) return null;
            if (t.Namespace != ProxyNamespace) return null;
            if (!t.IsGenericType) return null;
            if (!t.Name.StartsWith(ProxyNamePrefix, StringComparison.Ordinal)) return null;
            Type[] args = t.GetGenericArguments();
            return args.Length == 1 ? args[0] : null;
        }

        /// <summary>
        /// True when <paramref name="v"/> is an Il2CppSystem.Nullable&lt;T&gt;.
        /// <paramref name="inner"/> is the payload, or null when the nullable holds no value.
        /// </summary>
        internal static bool TryUnwrap(object v, out object inner)
        {
            inner = null;
            if (v == null) return false;

            Type t = v.GetType();
            if (t.Namespace != ProxyNamespace) return false;
            if (!t.IsGenericType) return false;
            if (!t.Name.StartsWith(ProxyNamePrefix, StringComparison.Ordinal)) return false;

            object hv;
            if (!TryReadMember(v, "hasValue", out hv))
            {
                if (!TryReadMember(v, "HasValue", out hv)) return false;
            }

            bool hasValue;
            try { hasValue = Convert.ToBoolean(hv, CultureInfo.InvariantCulture); }
            catch { return false; }

            if (!hasValue) return true;   // it IS a nullable, and it holds no value

            object val;
            if (!TryReadMember(v, "value", out val))
            {
                if (!TryReadMember(v, "Value", out val)) return false;
            }
            inner = val;
            return true;
        }

        private static bool TryReadMember(object target, string name, out object value)
        {
            value = null;
            if (target == null) return false;
            Type t = target.GetType();
            PropertyInfo p = t.GetProperty(name, PubInst) ?? t.GetProperty(name, AnyInst);
            if (p != null && p.CanRead) { value = p.GetValue(target); return true; }
            FieldInfo f = t.GetField(name, PubInst) ?? t.GetField(name, AnyInst);
            if (f != null) { value = f.GetValue(target); return true; }
            return false;
        }
    }
}
