# Copyright (c) 2003-2015 by Mike Jarvis
#
# TreeCorr is free software: redistribution and use in source and binary forms,
# with or without modification, are permitted provided that the following
# conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions, and the disclaimer given in the accompanying LICENSE
#    file.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions, and the disclaimer given in the documentation
#    and/or other materials provided with the distribution.

"""
.. module:: ngcorrelation
"""

import treecorr
import numpy as np


class NGCorrelation(treecorr.BinnedCorr2):
    """This class handles the calculation and storage of a 2-point count-shear correlation
    function.  This is the tangential shear profile around lenses, commonly referred to as
    galaxy-galaxy lensing.

    Ojects of this class holds the following attributes:

        :nbins:     The number of bins in logr
        :bin_size:  The size of the bins in logr
        :min_sep:   The minimum separation being considered
        :max_sep:   The maximum separation being considered

    In addition, the following attributes are numpy arrays of length (nbins):

        :logr:      The nominal center of the bin in log(r) (the natural logarithm of r).
        :rnom:      The nominal center of the bin converted to regular distance.
                    i.e. r = exp(logr).
        :meanr:     The (weighted) mean value of r for the pairs in each bin.
                    If there are no pairs in a bin, then exp(logr) will be used instead.
        :meanlogr:  The (weighted) mean value of log(r) for the pairs in each bin.
                    If there are no pairs in a bin, then logr will be used instead.
        :xi:        The correlation function, :math:`\\xi(r) = \\langle \\gamma_T\\rangle`.
        :xi_im:     The imaginary part of :math:`\\xi(r)`.
        :varxi:     The variance of :math:`\\xi`, only including the shape noise propagated into
                    the final correlation.  This does not include sample variance, so it is
                    always an underestimate of the actual variance.
        :weight:    The total weight in each bin.
        :npairs:    The number of pairs going into each bin.

    If `sep_units` are given (either in the config dict or as a named kwarg) then the distances
    will all be in these units.  Note however, that if you separate out the steps of the
    :func:`process` command and use :func:`process_auto` and/or :func:`process_cross`, then the
    units will not be applied to :meanr: or :meanlogr: until the :func:`finalize` function is
    called.

    The typical usage pattern is as follows:

        >>> ng = treecorr.NGCorrelation(config)
        >>> ng.process(cat1,cat2)   # Compute the cross-correlation.
        >>> ng.write(file_name)     # Write out to a file.
        >>> xi = gg.xi              # Or access the correlation function directly.

    :param config:      A configuration dict that can be used to pass in kwargs if desired.
                        This dict is allowed to have addition entries in addition to those listed
                        in :class:`~treecorr.BinnedCorr2`, which are ignored here. (default: None)
    :param logger:      If desired, a logger object for logging. (default: None, in which case
                        one will be built according to the config dict's verbose level.)

    See the documentation for :class:`~treecorr.BinnedCorr2` for the list of other allowed kwargs,
    which may be passed either directly or in the config dict.
    """
    def __init__(self, config=None, logger=None, **kwargs):
        treecorr.BinnedCorr2.__init__(self, config, logger, **kwargs)

        self._d1 = 1  # NData
        self._d2 = 3  # GData
        self.xi = np.zeros_like(self.rnom, dtype=float)
        self.xi_im = np.zeros_like(self.rnom, dtype=float)
        self.varxi = np.zeros_like(self.rnom, dtype=float)
        self.meanr = np.zeros_like(self.rnom, dtype=float)
        self.meanlogr = np.zeros_like(self.rnom, dtype=float)
        self.weight = np.zeros_like(self.rnom, dtype=float)
        self.npairs = np.zeros_like(self.rnom, dtype=float)
        self._build_corr()
        self.logger.debug('Finished building NGCorr')

    def _build_corr(self):
        from treecorr.util import double_ptr as dp
        self.corr = treecorr._lib.BuildCorr2(
                self._d1, self._d2, self._bintype,
                self._min_sep,self._max_sep,self._nbins,self._bin_size,self.b,
                self.min_rpar, self.max_rpar,
                dp(self.xi),dp(self.xi_im), dp(None), dp(None),
                dp(self.meanr),dp(self.meanlogr),dp(self.weight),dp(self.npairs));

    def __del__(self):
        # Using memory allocated from the C layer means we have to explicitly deallocate it
        # rather than being able to rely on the Python memory manager.
        # In case __init__ failed to get that far
        if hasattr(self,'corr'):  # pragma: no branch
            treecorr._lib.DestroyCorr2(self.corr, self._d1, self._d2, self._bintype)

    def __eq__(self, other):
        return (isinstance(other, NGCorrelation) and
                self.nbins == other.nbins and
                self.bin_size == other.bin_size and
                self.min_sep == other.min_sep and
                self.max_sep == other.max_sep and
                self.sep_units == other.sep_units and
                self.coords == other.coords and
                self.bin_type == other.bin_type and
                self.bin_slop == other.bin_slop and
                self.min_rpar == other.min_rpar and
                self.max_rpar == other.max_rpar and
                np.array_equal(self.meanr, other.meanr) and
                np.array_equal(self.meanlogr, other.meanlogr) and
                np.array_equal(self.xi, other.xi) and
                np.array_equal(self.xi_im, other.xi_im) and
                np.array_equal(self.varxi, other.varxi) and
                np.array_equal(self.weight, other.weight) and
                np.array_equal(self.npairs, other.npairs))

    def copy(self):
        import copy
        return copy.deepcopy(self)

    def __getstate__(self):
        d = self.__dict__.copy()
        del d['corr']
        del d['logger']  # Oh well.  This is just lost in the copy.  Can't be pickled.
        return d

    def __setstate__(self, d):
        self.__dict__ = d
        self._build_corr()
        self.logger = treecorr.config.setup_logger(
                treecorr.config.get(self.config,'verbose',int,0),
                self.config.get('log_file',None))

    def __repr__(self):
        return 'NGCorrelation(config=%r)'%self.config

    def process_cross(self, cat1, cat2, metric=None, num_threads=None):
        """Process a single pair of catalogs, accumulating the cross-correlation.

        This accumulates the weighted sums into the bins, but does not finalize
        the calculation by dividing by the total weight at the end.  After
        calling this function as often as desired, the finalize() command will
        finish the calculation.

        :param cat1:        The first catalog to process
        :param cat2:        The second catalog to process
        :param metric:      Which metric to use.  See :meth:`~treecorr.NGCorrelation.process` for
                            details.  (default: 'Euclidean'; this value can also be given in the
                            constructor in the config dict.)
        :param num_threads: How many OpenMP threads to use during the calculation.
                            (default: use the number of cpu cores; this value can also be given in
                            the constructor in the config dict.) Note that this won't work if the
                            system's C compiler is clang prior to version 3.7.
        """
        if cat1.name == '' and cat2.name == '':
            self.logger.info('Starting process NG cross-correlations')
        else:
            self.logger.info('Starting process NG cross-correlations for cats %s, %s.',
                             cat1.name, cat2.name)

        self._set_metric(metric, cat1.coords, cat2.coords)

        self._set_num_threads(num_threads)

        min_size, max_size = self._get_minmax_size()

        f1 = cat1.getNField(min_size, max_size, self.split_method,
                            self.brute is True or self.brute is 1,
                            self.max_top, self.coords)
        f2 = cat2.getGField(min_size, max_size, self.split_method,
                            self.brute is True or self.brute is 2,
                            self.max_top, self.coords)

        self.logger.info('Starting %d jobs.',f1.nTopLevelNodes)
        treecorr._lib.ProcessCross2(self.corr, f1.data, f2.data, self.output_dots,
                                    f1._d, f2._d, self._coords, self._bintype, self._metric)


    def process_pairwise(self, cat1, cat2, metric=None, num_threads=None):
        """Process a single pair of catalogs, accumulating the cross-correlation, only using
        the corresponding pairs of objects in each catalog.

        This accumulates the weighted sums into the bins, but does not finalize
        the calculation by dividing by the total weight at the end.  After
        calling this function as often as desired, the finalize() command will
        finish the calculation.

        :param cat1:        The first catalog to process
        :param cat2:        The second catalog to process
        :param metric:      Which metric to use.  See :meth:`~treecorr.NGCorrelation.process` for
                            details.  (default: 'Euclidean'; this value can also be given in the
                            constructor in the config dict.)
        :param num_threads: How many OpenMP threads to use during the calculation.
                            (default: use the number of cpu cores; this value can also be given in
                            the constructor in the config dict.) Note that this won't work if the
                            system's C compiler is clang prior to version 3.7.
        """
        if cat1.name == '' and cat2.name == '':
            self.logger.info('Starting process NG pairwise-correlations')
        else:
            self.logger.info('Starting process NG pairwise-correlations for cats %s, %s.',
                             cat1.name, cat2.name)

        self._set_metric(metric, cat1.coords, cat2.coords)

        self._set_num_threads(num_threads)

        f1 = cat1.getNSimpleField()
        f2 = cat2.getGSimpleField()

        treecorr._lib.ProcessPair(self.corr, f1.data, f2.data, self.output_dots,
                                  f1._d, f2._d, self._coords, self._bintype, self._metric)


    def finalize(self, varg):
        """Finalize the calculation of the correlation function.

        The process_cross command accumulates values in each bin, so it can be called
        multiple times if appropriate.  Afterwards, this command finishes the calculation
        by dividing each column by the total weight.

        :param varg:    The shear variance per component for the second field.
        """
        mask1 = self.weight != 0
        mask2 = self.weight == 0

        self.xi[mask1] /= self.weight[mask1]
        self.xi_im[mask1] /= self.weight[mask1]
        self.meanr[mask1] /= self.weight[mask1]
        self.meanlogr[mask1] /= self.weight[mask1]
        self.varxi[mask1] = varg / self.weight[mask1]

        # Update the units of meanr, meanlogr
        self._apply_units(mask1)

        # Use meanr, meanlogr when available, but set to nominal when no pairs in bin.
        self.meanr[mask2] = self.rnom[mask2]
        self.meanlogr[mask2] = self.logr[mask2]
        self.varxi[mask2] = 0.


    def clear(self):
        """Clear the data vectors
        """
        self.xi.ravel()[:] = 0
        self.xi_im.ravel()[:] = 0
        self.varxi.ravel()[:] = 0
        self.meanr.ravel()[:] = 0
        self.meanlogr.ravel()[:] = 0
        self.weight.ravel()[:] = 0
        self.npairs.ravel()[:] = 0

    def __iadd__(self, other):
        """Add a second NGCorrelation's data to this one.

        Note: For this to make sense, both Correlation objects should have been using
        process_cross, and they should not have had finalize called yet.
        Then, after adding them together, you should call finalize on the sum.
        """
        if not isinstance(other, NGCorrelation):
            raise AttributeError("Can only add another NGCorrelation object")
        if not (self._nbins == other._nbins and
                self.min_sep == other.min_sep and
                self.max_sep == other.max_sep):
            raise ValueError("NGCorrelation to be added is not compatible with this one.")

        self._set_metric(other.metric, other.coords)
        self.xi.ravel()[:] += other.xi.ravel()[:]
        self.xi_im.ravel()[:] += other.xi_im.ravel()[:]
        self.varxi.ravel()[:] += other.varxi.ravel()[:]
        self.meanr.ravel()[:] += other.meanr.ravel()[:]
        self.meanlogr.ravel()[:] += other.meanlogr.ravel()[:]
        self.weight.ravel()[:] += other.weight.ravel()[:]
        self.npairs.ravel()[:] += other.npairs.ravel()[:]
        return self


    def process(self, cat1, cat2, metric=None, num_threads=None):
        """Compute the correlation function.

        Both arguments may be lists, in which case all items in the list are used
        for that element of the correlation.

        :param cat1:    A catalog or list of catalogs for the N field.
        :param cat2:    A catalog or list of catalogs for the G field.
        :param metric:  Which metric to use for distance measurements.  Options are given
                        in the doc string of :class:`~treecorr.BinnedCorr2`.
                        (default: 'Euclidean'; this value can also be given in the constructor
                        in the config dict.)
        :param num_threads: How many OpenMP threads to use during the calculation.
                            (default: use the number of cpu cores; this value can also be given in
                            the constructor in the config dict.) Note that this won't work if the
                            system's C compiler is clang prior to version 3.7.
        """
        import math
        self.clear()

        if not isinstance(cat1,list): cat1 = [cat1]
        if not isinstance(cat2,list): cat2 = [cat2]

        varg = treecorr.calculateVarG(cat2)
        self.logger.info("varg = %f: sig_sn (per component) = %f",varg,math.sqrt(varg))
        self._process_all_cross(cat1,cat2,metric,num_threads)
        self.finalize(varg)


    def calculateXi(self, rg=None):
        """Calculate the correlation function possibly given another correlation function
        that uses random points for the foreground objects.

        - If rg is None, the simple correlation function :math:`\\langle \\gamma_T\\rangle` is
          returned.
        - If rg is not None, then a compensated calculation is done:
          :math:`\\langle \\gamma_T\\rangle = (DG - RG)`

        :param rg:          An NGCorrelation using random locations as the lenses, if desired.
                            (default: None)

        :returns:           (xi, xi_im, varxi) as a tuple.
        """
        if rg is None:
            return self.xi, self.xi_im, self.varxi
        else:
            return (self.xi - rg.xi), (self.xi_im - rg.xi_im), (self.varxi + rg.varxi)


    def write(self, file_name, rg=None, file_type=None, precision=None):
        """Write the correlation function to the file, file_name.

        - If rg is None, the simple correlation function :math:`\\langle \\gamma_T\\rangle` is used.
        - If rg is not None, then a compensated calculation is done:
          :math:`\\langle \\gamma_T\\rangle = (DG - RG)`

        The output file will include the following columns:

            :R_nom:     The nominal center of the bin in R.
            :meanR:     The mean value :math:`\\langle R\\rangle` of pairs that fell into each bin.
            :meanlogR:  The mean value :math:`\\langle logR\\rangle` of pairs that fell into each
                        bin.
            :gamT:      The real part of the mean tangential shear
                        :math:`\\langle \\gamma_T\\rangle(R)`.
            :gamX:      The imag part of the mean tangential shear
                        :math:`\\langle \\gamma_T\\rangle(R)`.
            :sigma:     The sqrt of the variance estimate of :math:\\langle \\gamma_T\\rangle`
            :weight:    The total weight contributing to each bin.
            :npairs:    The number of pairs contributing ot each bin.

        If `sep_units` was given at construction, then the distances will all be in these units.
        Otherwise, they will be in either the same units as x,y,z (for flat or 3d coordinates) or
        radians (for spherical coordinates).

        :param file_name:   The name of the file to write to.
        :param rg:          An NGCorrelation using random locations as the lenses, if desired.
                            (default: None)
        :param file_type:   The type of file to write ('ASCII' or 'FITS').  (default: determine
                            the type automatically from the extension of file_name.)
        :param precision:   For ASCII output catalogs, the desired precision. (default: 4;
                            this value can also be given in the constructor in the config dict.)
        """
        self.logger.info('Writing NG correlations to %s',file_name)

        xi, xi_im, varxi = self.calculateXi(rg)
        if precision is None:
            precision = self.config.get('precision', 4)

        params = { 'coords' : self.coords, 'metric' : self.metric,
                   'sep_units' : self.sep_units, 'bin_type' : self.bin_type }

        treecorr.util.gen_write(
            file_name,
            ['R_nom','meanR','meanlogR','gamT','gamX','sigma','weight','npairs'],
            [ self.rnom, self.meanr, self.meanlogr,
              xi, xi_im, np.sqrt(varxi), self.weight, self.npairs ],
            params=params, precision=precision, file_type=file_type, logger=self.logger)


    def read(self, file_name, file_type=None):
        """Read in values from a file.

        This should be a file that was written by TreeCorr, preferably a FITS file, so there
        is no loss of information.

        Warning: The NGCorrelation object should be constructed with the same configuration
        parameters as the one being read.  e.g. the same min_sep, max_sep, etc.  This is not
        checked by the read function.

        :param file_name:   The name of the file to read in.
        :param file_type:   The type of file ('ASCII' or 'FITS').  (default: determine the type
                            automatically from the extension of file_name.)
        """
        self.logger.info('Reading NG correlations from %s',file_name)

        data, params = treecorr.util.gen_read(file_name, file_type=file_type, logger=self.logger)
        self.rnom = data['R_nom']
        self.logr = np.log(self.rnom)
        self.meanr = data['meanR']
        self.meanlogr = data['meanlogR']
        self.xi = data['gamT']
        self.xi_im = data['gamX']
        self.varxi = data['sigma']**2
        self.weight = data['weight']
        self.npairs = data['npairs']
        self.coords = params['coords'].strip()
        self.metric = params['metric'].strip()
        self.sep_units = params['sep_units'].strip()
        self.bin_type = params['bin_type'].strip()
        self._build_corr()


    def calculateNMap(self, rg=None, m2_uform=None):
        """Calculate the aperture mass statistics from the correlation function.

        .. math::

            \\langle N M_{ap} \\rangle(R) &= \\int_{0}^{rmax} \\frac{r dr}{R^2}
            T_\\times\\left(\\frac{r}{R}\\right) \\Re\\xi(r) \\\\
            \\langle N M_{\\times} \\rangle(R) &= \\int_{0}^{rmax} \\frac{r dr}{R^2}
            T_\\times\\left(\\frac{r}{R}\\right) \\Im\\xi(r)

        The m2_uform parameter sets which definition of the aperture mass to use.
        The default is to use 'Crittenden'.

        If m2_uform == 'Crittenden':

        .. math::

            U(r) &= \\frac{1}{2\\pi} (1-r^2) \\exp(-r^2/2) \\\\
            T_\\times(s) = \\frac{s^2}{128} (12-s^2) \\exp(-s^2/4)

        cf. Crittenden, et al (2002): ApJ, 568, 20

        If m2_uform == 'Schneider':

        .. math::

            U(r) &= \\frac{9}{\\pi} (1-r^2) (1/3-r^2) \\\\
            T_\\times(s) = \\frac{18}{\\pi} s^2 \\arccos(s/2) -
            \\frac{3}{40\\pi} s^3 \\sqrt{4-s^2} (196 - 74s^2 + 14s^4 - s^6)

        cf. Schneider, et al (2002): A&A, 389, 729

        In neither case is this formula in the above papers, but the derivation is similar
        to the derivations of T+ and T- in Schneider et al.

        :param rg:          An NGCorrelation using random locations as the lenses, if desired.
                            (default: None)
        :param m2_uform:    Which form to use for the aperture mass, as described above.
                            (default: 'Crittenden'; this value can also be given in the
                            constructor in the config dict.)

        :returns:           (nmap, nmx, varnmap) as a tuple
        """
        if m2_uform is None:
            m2_uform = treecorr.config.get(self.config,'m2_uform',str,'Crittenden')
        if m2_uform not in ['Crittenden', 'Schneider']:
            raise ValueError("Invalid m2_uform")

        # Make s a matrix, so we can eventually do the integral by doing a matrix product.
        r = self.rnom
        s = np.outer(1./r, self.meanr)
        ssq = s*s
        if m2_uform == 'Crittenden':
            exp_factor = np.exp(-ssq/4.)
            Tx = ssq * (12. - ssq) / 128. * exp_factor
        else:
            Tx = np.zeros_like(s)
            sa = s[s<2.]
            ssqa = ssq[s<2.]
            Tx[s<2.] = 196. + ssqa*(-74. + ssqa*(14. - ssqa))
            Tx[s<2.] *= -3./(40.*np.pi) * sa * ssqa * np.sqrt(4.-sa**2)
            Tx[s<2.] += 18./np.pi * ssqa * np.arccos(sa/2.)
        Tx *= ssq

        xi, xi_im, varxi = self.calculateXi(rg)

        # Now do the integral by taking the matrix products.
        # Note that dlogr = bin_size
        Txxi = Tx.dot(xi)
        Txxi_im = Tx.dot(xi_im)
        nmap = Txxi * self.bin_size
        nmx = Txxi_im * self.bin_size

        # The variance of each of these is
        # Var(<NMap>(R)) = int_r=0..2R [s^4 dlogr^2 Tx(s)^2 Var(xi)]
        varnmap = (Tx**2).dot(varxi) * self.bin_size**2

        return nmap, nmx, varnmap


    def writeNMap(self, file_name, rg=None, m2_uform=None, file_type=None, precision=None):
        """Write the cross correlation of the foreground galaxy counts with the aperture mass
        based on the correlation function to the file, file_name.

        If rg is provided, the compensated calculation will be used for :math:`\\xi`.

        See :meth:`~treecorr.NGCorrelation:calculateNMap` for an explanation of the `m2_uform`
        parameter.

        The output file will include the following columns:

            :R:         The radius of the aperture.
            :NMap:      The mean value :math:`\\langle N_{ap} M_{ap}\\rangle`.
            :NMx:       The mean value :math:`\\langle N_{ap} M_x\\rangle`.
            :sig_nmap:  The sqrt of the variance estimate of :math:`\\langle N_{ap} M_{ap}\\rangle`
                        (which is the same as that of :math:`\\langle N_{ap} M_x\\rangle`.


        :param file_name:   The name of the file to write to.
        :param rg:          An NGCorrelation using random locations as the lenses, if desired.
                            (default: None)
        :param m2_uform:    Which form to use for the aperture mass.  (default: 'Crittenden';
                            this value can also be given in the constructor in the config dict.)
        :param file_type:   The type of file to write ('ASCII' or 'FITS').  (default: determine
                            the type automatically from the extension of file_name.)
        :param precision:   For ASCII output catalogs, the desired precision. (default: 4;
                            this value can also be given in the constructor in the config dict.)
        """
        self.logger.info('Writing NMap from NG correlations to %s',file_name)

        nmap, nmx, varnmap = self.calculateNMap(rg=rg, m2_uform=m2_uform)
        if precision is None:
            precision = self.config.get('precision', 4)

        treecorr.util.gen_write(
            file_name,
            ['R','NMap','NMx','sig_nmap'],
            [ self.rnom, nmap, nmx, np.sqrt(varnmap) ],
            precision=precision, file_type=file_type, logger=self.logger)


    def writeNorm(self, file_name, gg, dd, rr, dr=None, rg=None, m2_uform=None, file_type=None,
                  precision=None):
        """Write the normalized aperture mass cross-correlation to the file, file_name.

        The combination :math:`\\langle N M_{ap}\\rangle^2 / \\langle M_{ap}^2\\rangle
        \\langle N_{ap}^2\\rangle` is related to :math:`r`, the galaxy-mass correlation
        coefficient.  Similarly, :math:`\\langle N_{ap}^2\\rangle / \\langle M_{ap}^2\\rangle`
        is related to :math:`b`, the galaxy bias parameter.  cf. Hoekstra et al, 2002:
        http://adsabs.harvard.edu/abs/2002ApJ...577..604H

        This function computes these combinations and outputs them to a file.

        - if rg is provided, the compensated calculation will be used for
          :math:`\\langle N_{ap} M_{ap} \\rangle`.
        - if dr is provided, the compensated calculation will be used for
          :math:`\\langle N_{ap}^2 \\rangle`.

        See :meth:`~treecorr.NGCorrelation.calculateNMap` for an explanation of the m2_uform
        parameter.

        The output file will include the following columns:

            :R:             The radius of the aperture.
            :NMap:          The mean value :math:`\\langle N_{ap} M_{ap}\\rangle`.
            :NMx:           The mean value :math:`\\langle N_{ap} M_x\\rangle`.
            :sig_nmap:      The sqrt of the variance estimate of
                            :math:`\\langle N_{ap} M_{ap}\\rangle` or
                            :math:`\\langle N_{ap} M_x\\rangle`.
            :Napsq:         The mean value :math:`\\langle N_{ap}^2\\rangle`.
            :sig_napsq:     The sqrt of the variance estimate of :math:`\\langle N_{ap}^2\\rangle`.
            :Mapsq:         The mean value :math:`\\langle M_{ap}^2\\rangle`.
            :sig_mapsq:     The sqrt of the variance estimate of :math:`\\langle M_{ap}^2\\rangle`.
            :NMap_norm:     The ratio :math:`\\langle N_{ap} M_{ap}\\rangle^2 /
                            (\\langle N_{ap}^2\\rangle\\langle M_{ap}^2\\rangle)`.
            :sig_norm:      The sqrt of the variance estimate of
                            :math:`\\langle N_{ap} M_{ap}\\rangle^2 /
                            (\\langle N_{ap}^2\\rangle\\langle M_{ap}^2\\rangle)`.
            :Nsq_Mapsq:     The ratio :math:`\\langle N_{ap}^2/\\langle M_{ap}^2\\rangle`.
            :sig_nn_mm:     The sqrt of the variance estimate of
                            :math:`\\langle N_{ap}^\\rangle/\\langle M_{ap}^2\\rangle`.


        :param file_name:   The name of the file to write to.
        :param gg:          A GGCorrelation object for the shear-shear correlation function
                            of the G field.
        :param dd:          An NNCorrelation object for the count-count correlation function
                            of the N field.
        :param rr:          An NNCorrelation object for the random-random pairs.
        :param dr:          An NNCorrelation object for the data-random pairs, if desired, in which
                            case the Landy-Szalay estimator will be calculated.  (default: None)
        :param rg:          An NGCorrelation using random locations as the lenses, if desired.
                            (default: None)
        :param m2_uform:    Which form to use for the aperture mass.  (default: 'Crittenden';
                            this value can also be given in the constructor in the config dict.)
        :param file_type:   The type of file to write ('ASCII' or 'FITS').  (default: determine
                            the type automatically from the extension of file_name.)
        :param precision:   For ASCII output catalogs, the desired precision. (default: 4;
                            this value can also be given in the constructor in the config dict.)
        """
        self.logger.info('Writing Norm from NG correlations to %s',file_name)

        nmap, nmx, varnmap = self.calculateNMap(rg=rg, m2_uform=m2_uform)
        mapsq, mapsq_im, mxsq, mxsq_im, varmapsq = gg.calculateMapSq(m2_uform=m2_uform)
        nsq, varnsq = dd.calculateNapSq(rr, dr=dr, m2_uform=m2_uform)

        nmnorm = nmap**2 / (nsq * mapsq)
        varnmnorm = nmnorm**2 * (4. * varnmap / nmap**2 + varnsq / nsq**2 + varmapsq / mapsq**2)
        nnnorm = nsq / mapsq
        varnnnorm = nnnorm**2 * (varnsq / nsq**2 + varmapsq / mapsq**2)
        if precision is None:
            precision = self.config.get('precision', 4)

        treecorr.util.gen_write(
            file_name,
            [ 'R',
              'NMap','NMx','sig_nmap',
              'Napsq','sig_napsq','Mapsq','sig_mapsq',
              'NMap_norm','sig_norm','Nsq_Mapsq','sig_nn_mm' ],
            [ self.rnom,
              nmap, nmx, np.sqrt(varnmap),
              nsq, np.sqrt(varnsq), mapsq, np.sqrt(varmapsq),
              nmnorm, np.sqrt(varnmnorm), nnnorm, np.sqrt(varnnnorm) ],
            precision=precision, file_type=file_type, logger=self.logger)

