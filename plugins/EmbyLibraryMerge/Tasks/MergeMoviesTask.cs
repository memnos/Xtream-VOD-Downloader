using EmbyLibraryMerge.Services;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Tasks;

namespace EmbyLibraryMerge.Tasks;

public class MergeMoviesTask : IScheduledTask
{
    private readonly DuplicateMergeService _mergeService;
    private readonly ILogger _logger;

    public MergeMoviesTask(ILibraryManager libraryManager, ILogger logger)
    {
        _logger = logger;
        _mergeService = new DuplicateMergeService(libraryManager, logger);
    }

    public string Name => "Unisci film duplicati (MKV + STRM)";

    public string Key => "EmbyLibraryMergeMovies";

    public string Description =>
        "Unisce film con lo stesso TMDB ID (cartella locale + STRM).";

    public string Category => "Library Merge";

    public IEnumerable<TaskTriggerInfo> GetDefaultTriggers()
    {
        yield break;
    }

    public Task Execute(CancellationToken cancellationToken, IProgress<double> progress)
    {
        _logger.Info("Library Merge: movie task started");
        var result = _mergeService.MergeMovies(progress, cancellationToken);
        _logger.Info("Library Merge: merged {0} movie groups", result.MovieGroupsMerged);
        progress.Report(100);
        return Task.CompletedTask;
    }
}
