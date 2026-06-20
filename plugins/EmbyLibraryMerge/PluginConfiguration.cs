using MediaBrowser.Model.Plugins;

namespace EmbyLibraryMerge;

public class PluginConfiguration : BasePluginConfiguration
{
    public bool PreferLocalMoviePaths { get; set; } = true;

    public bool PreferLocalSeriesPaths { get; set; } = true;

    /// <summary>Comma-separated path fragments to skip (optional).</summary>
    public string ExcludedPathContains { get; set; } = string.Empty;
}
