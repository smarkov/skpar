"""
Classes and functions related to the:

    * parsing the definition of objectives in the input file,
    * setting the objectives for the optimizer, and,
    * evaluation of objectives.
"""
import numpy as np
import yaml
from pprint import pprint, pformat
from skopt.utils import get_logger, normalise
from skopt.query import Query

from skopt.evaluate import costf, errf
DEFAULT_COST_FUNC = "rms"
DEFAULT_ERROR_FUNC = "abs"

def parse_weights_keyval(spec, data, normalised=True):
    """Parse the weights corresponding to key-value type of data.

    Args:
        spec (dict): Specification of weights, in a key-value fashion.
            It is in the example format::
                { 'dflt': 0., 'key1': w1, 'key3': w3}
            with w1, w3, etc. being float values.
        data (structured numpy array): Data to be weighted.

            Typical way of obtaining `data` in this format is to use::

                loader_args = {'dtype': [('keys', 'S15'), ('values', 'float')]}
                data = numpy.loadtxt(file, **loader_args)

    Returns:
        numpy.array: weights corresponding to each key in `data`, 
            with the same length as `data`.
    TODO:
        Log warning if a key in `spec` (other than 'dflt') is not found
        in `data`.
    """
    if isinstance(spec, list) or type(spec).__module__ == np.__name__:
        # if spec enumerates weights as a list or array, nothing to do
        assert len(spec)==len(data) 
        ww = spec
    else:
        # otherwise parse specification to write out the weights
        # initialise default values
        dflt = spec.get('dflt', 0)
        # Key assumption: data is a structured array, where the keys 
        # are already encoded as b'string', hence the use of .encode() below.
        nn = len(data)
        ww = np.ones(nn)*dflt
        _keys, _values = data.dtype.names
        for key, val in spec.items():
            # notabene: the encode() makes a 'string' in b'string'
            ww[data[_keys]==key.encode()] = val
    # normalisation
    if normalised:
        ww = normalise(ww)
    return ww

def parse_weights(spec, refdata=None, nn=1, shape=None, i0=0, normalised=True, 
                  ikeys=None, rikeys=None, rfkeys=None):
    """Parse the specification defining weights corresponding to some data.

    The data may or may not be specified, depending on the type of
    specification that is provided. Generally, the specification would
    enumerate either explicit indexes in the data, or a range of
    indexes in the data or a range of values in the data, and would
    associate a weight with the given range.
    A list of floats is also accepted, and an array view is returned,
    for cases where weights are explicitly enumerated, but no check for length.
    
    To give freedom of the user (i.e. the caller), the way that ranges
    are specified is enumerated by the caller by optional arguments --
    see `ikeys`, `rikeys` and `rfkeys` below.
    
    Args:
        spec (array-like or dict): values or specification of the subweights, 
            for example::
            spec = """ """
            dflt: 1.0 # default value of subweights
            indexes: # explicit [index, weight] for 1d-array data
                - [0, 1]
                - [4, 4]
                - [2, 1]
            ranges: # ranges for 1d-array
                - [[1,3], 2]
                - [[3,4], 5]
            bands: # ranges of bands (indexes) in bands (refdata)
                - [[-3, 0], 1.0] # all valence bands
                - [[0, 1], 2.0]   # top VB and bottom CB with higher weight
            values: # ranges of energies (values) in bands (refdata)
                - [[-0.1, 0.], 4.0]
                - [[0.2, 0.5], 6.0]
            indexes: # explicit (band, k-point) pair (indexes) for bands (refdata)
                - [[3, 4], 2.5]
                - [[1, 2], 3.5]
            """ """
        refdata (numpy.array): Reference data; mandatory only when range of
            values must be specified
        nn (int): length of `refdata` (and corresponding weights)
        shape (tuple): shape of `reference` data, if it is array but not given
        i0 (int): index to be assumed as a reference, i.e. 0, when 
            enumerating indexes explicitly or by a range specification.
        ikeys (list of strings): list of keys to be parsed for explicit 
            index specification, e.g. ['indexes', 'Ek']
        rikeys (list of strings): list of keys to be parsed for range of
            indexes specification, e.g. ['ranges', 'bands']
        rfkeys (list of strings): list of keys to be parsed for range of
            values specification, e.g. ['values', 'eV']

    Returns:
        numpy.array: the weight to be associated with each data item.
    """
    if ikeys is None:
        ikeys = []
    if rikeys is None:
        rikeys = []
    if rfkeys is None:
        rfkeys = []
    if isinstance(spec, list) and len(spec)==nn or\
        type(spec).__module__ == np.__name__:
        # Assume spec enumerates weights as a list or array
        ww = np.atleast_1d(spec)
    else:
        # Parse specification to write out the weights
        # initialise default values
        dflt = spec.get('dflt', 1)
        if shape is None:
            if refdata is not None:
                shape = refdata.shape
            else:
                shape = (nn,)
        assert shape is not None
        ww = np.ones(shape)*dflt
        # parse alterations for explicit data indexes
        # convert from FORTRAN to PYTHON, hence the -1 below
        for k in ikeys:
            for i, w in spec.get(k, []):
                try:
                    # assume i0 and i are int
                    ww[i0+i-1] = w
                except TypeError:
                    # if it turns out i is a tuple (i.e. an E-k point), 
                    # then apply the shift only to i[0].
                    # this works if we specify E-k point (band, k-point)
                    # but is somewhat restrictive in the more general context
                    j = (i0+i[0]-1, i[1]-1)
                    ww[j] = w
        # parse alterations for integer ranges of indexes
        for k in rikeys:
            for rngs, w in spec.get(k, []):
                rngs = get_ranges([rngs,])
                for ilo, ihi in rngs:
                    # permit overlapping ranges, larger weight overrides:
                    ww[ilo:ihi][ww[ilo:ihi] < w] = w
        # parse alterations for ranges in the reference data itself
        for k in rfkeys:
            assert refdata.shape == ww.shape
            for rng, w in spec.get(k, []):
                ww[(rng[0] <= refdata) & 
                   (refdata <= rng[1]) &
                   # permit overlapping weights, larger value overrides:
                   (ww < w)] = w
    # normalisation
    if normalised:
        ww = normalise(ww)
    return ww

def get_models(models):
    """Return the models (names) and corresponding weights if any.

    Args:
        models (str, list of str, list of [str: float] items): The 
            string is always a model name. If [str: float] items
            are given, the float has the meaning of weight, associated
            with the model.
    Returns:
        tuple: (model_names, model_weights). Weights
            are set to 1.0 if not found in `models`. Elements of
            the tuple are lists if `models` is a list.
    """
    m_names = []
    m_weights = []
    if isinstance(models, list):
        for mm in models:
            if isinstance(mm, list):
                m_names.append(mm[0])
                m_weights.append(mm[1])
            else:
                m_names.append(mm)
                m_weights.append(1.)
    else:
        m_names = models
        m_weights = 1.
    return m_names, m_weights

def get_type(n_models, ref, dflt_type='values'):
    """Establish the type of objective from attributes of reference and models.
    """
    obj_type = dflt_type
    # If we have more than one model but just one scalar as reference
    # obviously we need scalarization (reduction) routine. We assume
    # the simplest -- weighted sum type; other types must be explicitly 
    # stated
    if n_models > 1 and ref.shape == (1,):
        obj_type = 'weighted_sum' # other types of scalarization must be explicit
    # if we have key-value pairs, then we have key-value type
    if n_models == 1 and ref.ndim == 1 and \
        ref.dtype == [('keys','S15'), ('values','float')]:
        obj_type = 'keyval_pairs'
    # if we have 2D-array ref-data, then we have Bands type
    if n_models == 1 and ref.ndim == 2 and ref.dtype == 'float':
        obj_type = 'bands'
    return obj_type

def plot(data, weights=None, figsize=(6, 7), outfile=None, 
        Erange=None, krange=None):
    """Visual representation of the band-structure and weights.
    """
    fig, ax = plt.subplots(figsize=figsize)
    nb, nk = data.shape
    xx = np.arange(nk)
    ax.set_xlabel('$\mathbf{k}$-point')
    ax.set_ylabel('Energy (eV)')
    if Erange is not None:
        ax.set_ylim(Erange)
    if krange is not None:
        ax.set_xlim(krange)
    else:
        ax.set_xlim(np.min(xx), np.max(xx))
    ax.yaxis.set_minor_locator(AutoMinorLocator())
    if weights is not None and len(np.unique(weights))-1 > 0:
        color = cm.jet((weights-np.min(weights))/
                    (np.max(weights)-np.min(weights)))
    else:
        color = ['b']*nb
    for yy, cc in zip(data, color):
        ax.scatter(xx, yy, s=1.5, c=cc, edgecolor='None')
    if plotfile is not None:
        fig.savefig(outfile)
    return fig, ax


class Objective(object):
    """Decouples the declaration of an objective from its evaluation.

    Objectives are declared by human input data that defines:
        * reference data,
        * models - from which to obtain model data, and possibly model weights,
        * query - the way to obtaining data
        * model weights - relative contribution factor of each model,
        * options, e.g. to specify sub-weights of individual reference items,
        * relative weight of the objective, in the context of multi-objective
          optimisation.

    Instances are callable, and return a triplet of model data, reference data,
    and sub-weights of relative importance of the items within each data.
    """
    def __init__(self, spec, logger=None, **kwargs):
        """Instantiate the objective and set non-specific attributes.
        
        Must be extended to declare a Query and possibly -- CostFunction.
        By 'extend', we mean super().__init__() is called within the 
        child's own __init__().
        That however should be done in a way that is specific to the
        type of objective.

        Args:
            spec (dict): Specification of the objective. Mandatory fields
                are [models, ref], optional keys are [weight, doc,
                options, model_options]
            logger (obj): Python logging.logger

        Returns: None
        """
        # mandatory fields
        self.objtype = spec['type']
        self.query_key = spec['query']
        self.model_names = spec['model_names']
        self.model_weights = spec['model_weights']
        self.ref_data = spec['ref_data']
        self.costf = costf[DEFAULT_COST_FUNC]
        self.errf = errf[DEFAULT_ERROR_FUNC]
        # optional fields
        self.weight = spec.get('weight', 1)
        self.options = spec.get('options', None)
        if self.options is not None:
            try:
                self.costf = costf[self.options['costf'].lower()]
            except KeyError:
                pass
            try:
                self.errf = errf[self.options['errf'].lower()]
            except KeyError:
                pass
        dfltdoc = "{}: {}".format(self.query_key, pformat(self.model_names))
        self.doc = spec.get('doc', dfltdoc)
        self.logger = get_logger(logger)
        # further definitions of set/get depend on type of objective
        # this may be set here or in a child, if more specific
        self.query = Query(self.model_names, self.query_key)
        self.subweights = np.ones(self.ref_data.shape)
              
    def get(self):
        """
        Return the corresponding model data, reference data, and sub-weights.
        This method must be overloaded in a child-class if a more
        specific way to yield the model data in required.
        """
        #
        assert self.model_data.shape == self.ref_data.shape,\
                    "{} {}".format(self.model_data.shape, self.ref_data.shape)
        assert self.model_data.shape == self.subweights.shape,\
                    "{} {}".format(self.model_data.shape, self.subweights.shape)
        #
        return self.model_data, self.ref_data, self.subweights

    def evaluate(self):
        """Evaluate objective, i.e. fitness of the current model against the reference."""
        model, ref, weights = self.get()
        fitness = self.costf(ref, model, weights, self.errf)
        return fitness
        
    def __call__(self):
        """Executes self.evaluate().
        """
        return self.evaluate()
    
    def __repr__(self):
        """Yield a summary of the objective.
        """
        s = []
        s.append("\n")
        s.append("{:<15s}: {}".format("Objective:", pformat(self.doc)))
        s.append("{:<15s}: {}".format("Query", pformat(self.query_key)))
        s.append("{:<15s}: {}".format("Models", pformat(self.model_names)))
        if hasattr(self, 'model_weights'):
            s.append("{:<15s}: {}".format("Model weights", pformat(self.model_weights)))
        s.append ("{:<15s}: {}".format("Reference data", pformat(self.ref_data)))
        if hasattr(self, 'subweights'):
            s.append("{:<15s}: {}".format("Sub-weights", pformat(self.subweights)))
        #s.append ("Options:\n{}".format(pformat(self.options)))
        if hasattr(self, 'model_data'):
            s.append ("Model data: {}".format(pformat(self.model_data)))
        s.append("{:<15s}: {:s} / {:s}".
                format("Cost/Err. func.", self.costf.__name__, self.errf.__name__))
        s.append("{:<15s}: {}".format("Weight", pformat(self.weight)))
        return "\n".join(s)


class ObjValues(Objective):
    """
    """
    def __init__(self, spec, logger=None, **kwargs):
        super().__init__(spec, logger, **kwargs)
        nmod = len(self.model_names)
        # coerce ref-data to 1D array if it is extracted from a 2D array
        if self.ref_data.ndim == 2 and self.ref_data.shape == (1,nmod):
            self.ref_data = self.ref_data.reshape((nmod,))
        shape = self.ref_data.shape
        if self.options is not None and 'subweights' in self.options.keys():
            self.normalised = self.options.get('normalise', True)
            self.subweights = parse_weights(self.options['subweights'], 
                    nn=nmod, normalised=self.normalised,
                    # these are optional, and generic enough
                    ikeys=["indexes",], rikeys=['ranges'], rfkeys=['values'])
            assert self.subweights.shape == shape, (self.subweights.shape, shape)
        else:
            self.subweight = np.ones(shape)
        
    def get(self):
        """
        """
        self.model_data = self.query()
        if len(self.model_names) > 1:
            assert self.model_data.shape == self.subweights.shape,\
                    "{} {}".format(self.model_data.shape, self.subweights.shape)
        return super().get()


class ObjKeyValuePairs(Objective):
    """
    """
    def __init__(self, spec, logger=None, **kwargs):
        super().__init__(spec, logger, **kwargs)
        # parse reference data options
        self.options = spec.get('options', None)
        # NOTABENE: we will replace self.ref_data, trimming the 
        #           items with null weight
        nn = len(self.ref_data)
        normalised = self.options.get('normalise', True)
        ww = parse_weights_keyval(self.options['subweights'], data=self.ref_data,
                                    normalised=normalised)
        # eliminate ref_data items with zero subweights
        mask = np.where(ww != 0)
        self.query_key = [k.decode() for k in self.ref_data['keys'][mask]]
        self.ref_data = self.ref_data['values'][mask]
        self.subweights = ww[mask]
        assert self.subweights.shape == self.ref_data.shape
        assert len(self.query_key) == len(self.ref_data)
        self.queries = []
        for key in self.query_key:
            self.queries.append(Query(self.model_names, key))
            
    def get(self):
        self.model_data = np.empty(self.ref_data.shape)
        for ix, query in enumerate(self.queries):
            self.model_data[ix] = (query())
        return super().get()


class ObjWeightedSum(Objective):
    """
    """
    def get(self):
        """
        """
        summands = self.query()
        assert len(summands) == len(self.model_weights)
        self.model_data = np.atleast_1d(np.dot(summands, self.model_weights))
        return super().get()


def get_subset_ind(rangespec):
    """Return an index array based on a spec -- a list of ranges.
    """
    pyrangespec = get_ranges(rangespec)
    subset = []
    for rr in pyrangespec:
        subset.extend(range(*rr))
    return np.array(subset)

def get_refval(bands, refpt, ff={'min': np.min, 'max': np.max}):
    """Return a reference (alignment) value selected from a 2D array.
    
    Args:
        bands (2D numpy array): data from which to obtain a reference value.
        refpt: specifier that could be (band-index, k-point), or
                (band-index, function), e.g. (3, 'min'), or ('7, 'max')
        ff (dict): Dictionary mapping strings names to functions that can
                operate on an 1D array.

    Returns:
        value (float): the selected value
    """
    iband = refpt[0] - 1  
    try:
        ik = refpt[1] - 1
        value = bands[iband,ik]
    except TypeError:
        value = ff[refpt[1]](bands[iband])
    return value


class ObjBands(Objective):
    """
    """
    def __init__(self, spec, logger=None, **kwargs):
        super().__init__(spec, logger, **kwargs)
        assert isinstance(self.model_names, str),\
            'ObjBands accepts only one model, but models is not a string'

        # Procss .options:
        # if options is not defined, NameError will result
        # if options is None (default, actually), TypeError will result
        # if options is missing a key we try, KeyError will result
        # Start with 'use_*' clauses
        try:
            rangespec = self.options.get('use_ref')
            subset_ind = get_subset_ind(rangespec)
            # This returns a new array, and the old ref_data
            # is lost from here on. Do we care?
            self.ref_data = self.ref_data[subset_ind]
            # since we re-shape self.ref_data, we must reshape 
            # the corresponding subweights too.
            self.subweights = np.ones(self.ref_data.shape)
        except (TypeError, KeyError):
            pass
        # once the ref_data is trimmed, its reference value may be changed
        try:
            align_pnt = self.options.get('align_ref')
            shift = get_refval(self.ref_data, align_pnt)
            self.ref_data -= shift
        except (TypeError, KeyError):
            pass
        # Make up a mask to trim model_data 
        # Note that the mask is only for dim_0, i.e. to
        # be applied on the bands, over all k-pts, so it
        # is only one one-dimensional array.
        try:
            rangespec = self.options.get('use_model')
            self.subset_ind = get_subset_ind(rangespec)
        except (TypeError, KeyError):
            self.subset_ind = None
        # Prepare to shift the model_data values if required
        # The actual shift is applied in the self.get() method
        try:
            self.align_model = self.options.get('align_model')
        except (TypeError, KeyError):
            pass

        shape = self.ref_data.shape
        if self.options is not None and 'subweights' in self.options.keys():
            subwspec = self.options.get('subweights')
            self.normalised = self.options.get('normalise', True)
            self.subweights = parse_weights(subwspec, refdata=self.ref_data, 
                    normalised=self.normalised, 
                    # the following are optional, and generic enough
                    # "indexes" is for a point in a 2D array
                    # "bands" is for range of bands (rows), etc.
                    # "values" is for a range of values
                    # "krange" may be provided in the future (for column selection),
                    # but is not supported yet
                    ikeys=['indexes','Ekpts'], rikeys=['bands', 'iband'], rfkeys=['values'])
            assert self.subweights.shape == shape
        else:
            # KeyError if there is no 'use_ref'
            # TypeError if options == None (default)
            self.subweight = np.ones(shape)
        
    def get(self):
        """
        """
        # query data base
        self.model_data = self.query()
        # apply mask
        if self.subset_ind is not None:
            self.model_data = self.model_data[self.subset_ind]
        # apply shift
        if self.align_model is not None:
            shift = get_refval(self.model_data, self.align_model)
            self.model_data -= shift
        return super().get()


objectives_mapper = {
        'value'       : ObjValues,
        'values'      : ObjValues,
        'weighted_sum': ObjWeightedSum,
        'keyval_pairs': ObjKeyValuePairs,
        'bands'       : ObjBands,
        }

def f2prange(rng):
    """Convert fortran range definition to a python one.
    
    Args:
        rng (2-sequence): [low, high] index range boundaries, 
            inclusive, counting starts from 1.
            
    Returns:
        2-tuple: (low-1, high)
    """
    lo, hi = rng
    msg = "Invalid range specification {}, {}.".format(lo, hi)\
        + " Range should be of two integers, both being >= 1."
    assert lo >= 1 and hi>=lo, msg
    return lo-1, hi

def get_ranges(data):
    """Return list of tuples ready to use as python ranges.

    Args:
        data (int, list of int, list of lists of int):
            A single index, a list of indexes, or a list of
            2-tuple range of indexes in Fortran convention,
            i.e. from low to high, counting from 1, and inclusive
    
    Return:
        list of lists of 2-tuple ranges, in Python convention -
        from 0, exclusive.
    """
    try:
        rngs = []
        for rng in data:
            try:
                lo, hi = rng
            except TypeError:
                lo, hi = rng, rng
            rngs.append(f2prange((lo, hi)))
    except TypeError:
        # data not iterable -> single index, convert to list of lists
        rngs = [f2prange((data,data))]
    return rngs

def get_refdata(data):
    """Parse the input data and return a corresponding array.

    Args:
        data (array or array-like, or a dict): Data, being the 
            reference data itself, or a specification of how to get
            the reference data. If dictionary, it should either
            contain key-value pairs of reference items, or contain
            a 'file' key, storing the reference data.

    Returns:
        array: an array of reference data array, subject to all loading 
            and post-processing of a data file, or pass `data` itself,
            transforming it to an array as necessary.
    """
    try:
        # assume `data` contains an instruction where/how to obtain values
        # ----------------------------------------------------------------------
        # NOTABENE:
        # The line below leads to "DeprecationWarning: using a # non-integer 
        # number instead of an integer will result in an error in the future"
        # if `data` happens to be a numpy array already.
        # Some explicit type-checking may be necessary as a rework, since
        # currently we rely on IndexError exception to catch if `data` is 
        # actually an array, but it is not clear if the same exception will be
        # raised in the future.
        # ----------------------------------------------------------------------
        file = data['file']
        # actual data in file -> load it
        # set default loader_args, assuming 'column'-organised data
        loader_args = {} #{'unpack': False}
        # overwrite defaults and add new loader_args
        loader_args.update(data.get('loader_args', {}))
        # make sure we don't try to unpack a key-value data
        if 'dtype' in loader_args.keys() and\
            'names' in loader_args['dtype']:
                loader_args['unpack'] = False
        # read file
        array_data = np.loadtxt(file, **loader_args)
        # do some filtering on columns and/or rows if requested
        # note that file to 2D-array mapping depends on 'unpack' from
        # loader_args, which transposes the loaded array.
        postprocess = data.get('process', {})
        if postprocess:
            if 'unpack' in loader_args.keys() and loader_args['unpack']:
                # since 'unpack' transposes the array, now row index
                # in the original file is along axis 1, while column index
                # in the original file is along axis 0.
                key1, key2 = ['rm_columns', 'rm_rows']
            else:
                key1, key2 = ['rm_rows', 'rm_columns']
            for axis, key in enumerate([key1, key2]):
                rm_rngs = postprocess.get(key, [])
                if rm_rngs:
                    indexes=[]
                    # flatten, combine and sort, then delete corresp. object
                    for rng in get_ranges(rm_rngs):
                        indexes.extend(list(range(*rng)))
                    indexes = list(set(indexes))
                    indexes.sort()
                    array_data = np.delete(array_data, obj=indexes, axis=axis)
            scale = postprocess.get('scale', 1)
            array_data = array_data * scale
        return array_data

    except KeyError:
        # `data` is a dict, but 'file' is not in its keys; assume that
        # `data` is a dict of key-value data -> transform to structured array
        dtype = [('keys','S15'), ('values','float')]
        return np.array([(key,val) for key,val in data.items()], dtype=dtype)

    except TypeError:
        if not isinstance(data, dict):
        # `data` is a value or a list  -> return array
            return np.atleast_1d(data)
        else:
        # `file` was not understood
            print ('np.loadtxt cannot understand the contents of {}'\
                    .format(file))
            raise

    except IndexError:
        # `data` is already an array  -> return as is
        # unlikely scenario, since yaml cannot encode numpy array
        return data

def get_objective(spec, logger=None):
    """Return an instance of an objective, as defined in the input spec.

    Args:
        spec (dict): a dictionary with a single entry, being
            query: {dict with the spec of the objective}

    Returns:
        list: an instance of the Objective sub-class, corresponding
            an appropriate objective type.
    """
    (key, spec), = spec.items()
    # mandatory fields
    spec['query'] = spec.get('query', key)
    m_names, m_weights = get_models(spec['models'])
    spec['model_names'] = m_names
    spec['model_weights'] = np.atleast_1d(m_weights)
    spec['ref_data'] = get_refdata(spec['ref'])
    if isinstance(m_names, str):
        nmod = 1
    else:
        nmod = len(m_names)
    spec['type'] = spec.get('type', get_type(nmod, spec['ref_data']))
    #   print (spec['type'], spec['query'])
    objv = objectives_mapper.get(spec['type'], ObjValues)(spec, logger=logger)
    print (objv)
    return objv

def set_objectives(spec, logger=None):
    """Parse user specification of Objectives, and return a list of Objectives for evaluation.

    Args:
        spec (list): List of dictionaries, each dictionary being a,
            specification of an objective of a recognised type.

    Returns:
        list: a List of instances of the Objective sub-class, each 
            corresponding to a recognised objective type.
    """
    objectives = []
    # the spec list has definitions of different objectives
    for item in spec:
        objectives.append(get_objective(item))
    return objectives

