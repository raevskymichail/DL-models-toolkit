import tensorflow as tf
import math
import util
import numpy as np
import matplotlib.pyplot as plt
import sys
import os

# Adapted from M2 model from Max Welling's paper
class SSVAERegressorX():
    def __init__(self, input_dim, vae_dims, reg_dims, latent_dim, prediction_dim=1, verbose=False):
        # Reset all existing tensors
        tf.reset_default_graph()
        
        # Tracking data
        self.loss = {"loss_l":[],
                    "rec_l":[],
                    "kld_z_l":[],
                    "logdense_y_l":[],
                    "loss_ul":[],
                    "rec_ul":[],
                    "kld_z_ul":[],
                    "kld_y_ul":[],
                    "mse":[]}
        self.record = {"mus":[], "ys":[]}
        
        # Define some parameters
        self.e = 0
        self.verbose = verbose
        
        # Define dimensionality
        self.input_dim = input_dim
        self.vaedims = vae_dims
        self.regressordims = reg_dims
        self.latent_dim = latent_dim
        self.prediction_dim = prediction_dim
        self.alpha = 0.1
        
        # Building the graph
        self.built = False
        self.sesh = tf.Session()
        self.ops = self.build()
        self.sesh.run(tf.global_variables_initializer())
        
        # Recording
        writer = tf.summary.FileWriter('logs', self.sesh.graph)
        
    def build(self):
        # Placeholders for input and dropout probs.
        if self.built:
            return -1
        else:
            self.built = True
            
        #####################
        # Building networks #
        #####################
        
        # Defining inputs
        x_in_labeled = tf.placeholder(tf.float32, shape=[None, self.input_dim], name="x_in_labeled")
        x_in_unlabeled = tf.placeholder(tf.float32, shape=[None, self.input_dim], name="x_in_unlabeled")
        y = tf.placeholder(tf.float32, shape=[None, self.prediction_dim], name="y")
            
        # Building network q(z|x)
        z_mu_labeled, z_log_sigma_labeled = self.encoder(x_in_labeled,
                                                         output_dim=self.latent_dim,
                                                         dims = self.vaedims,
                                                         _is_train=True,
                                                         scope="encoder")
        z_mu_unlabeled, z_log_sigma_unlabeled = self.encoder(x_in_unlabeled,
                                                             output_dim=self.latent_dim,
                                                             dims = self.vaedims,
                                                             _is_train=True,
                                                             scope="encoder", 
                                                             reuse=True)
        
        # Building network q(y|x)
        y_mu_labeled, y_log_sigma_labeled = self.encoder(x_in_labeled,
                                                         output_dim=self.prediction_dim,
                                                         dims = self.regressordims,
                                                         _is_train=True,
                                                         scope="regressor")
        y_mu_unlabeled, y_log_sigma_unlabeled = self.encoder(x_in_unlabeled,
                                                             output_dim=self.prediction_dim,
                                                             dims = self.regressordims,
                                                             _is_train=True,
                                                             scope="regressor",
                                                             reuse=True)

        
        # Sampling from q(z|x) and q(y|x)
        z_labeled_sampled = self.sample(z_mu_labeled, z_log_sigma_labeled)
        y_labeled_sampled = self.sample(y_mu_labeled, y_log_sigma_labeled)
        
        z_unlabeled_sampled = self.sample(z_mu_unlabeled, z_log_sigma_unlabeled)
        y_unlabeled_sampled = self.sample(y_mu_unlabeled, y_log_sigma_unlabeled)
        
        # Buiding p(x|z,y) 
        decoder_in_labeled = tf.concat([z_labeled_sampled, y], axis=1)
        x_out_labeled = self.decoder(decoder_in_labeled,
                                     dims = self.vaedims,
                                     _is_train=True,
                                     scope="decoder")
        
        decoder_in_unlabeled = tf.concat([z_unlabeled_sampled, y_unlabeled_sampled], axis=1)
        x_out_unlabeled = self.decoder(decoder_in_unlabeled,
                                       dims = self.vaedims,
                                       _is_train=True,
                                       scope="decoder",
                                       reuse=True)
        
        # Building preventor network
        anti_prediction = self.preventor(z_labeled_sampled,
                                   _is_train=True,
                                   scope="preventor",
                                   reuse=False)
        
        #################
        # Defining loss #
        #################
        
        # Defining Loss for labeled data
        with tf.variable_scope("labeled_loss"):
            rec_loss_labeled = self.crossEntropy(x_out_labeled, x_in_labeled)
            kld_labeled = self.KLD(z_mu_labeled, z_log_sigma_labeled)
            density_labeled = self.gaussian_log_density(y, y_mu_labeled, y_log_sigma_labeled)
            labeled_loss = tf.reduce_mean(rec_loss_labeled + kld_labeled - self.alpha*density_labeled)
        
        # Defining loss for unlabeled data
        with tf.variable_scope("unlabeled_loss"):
            rec_loss_unlabeled = self.crossEntropy(x_out_unlabeled, x_in_unlabeled)
            kld_z_unlabeled = self.KLD(z_mu_unlabeled, z_log_sigma_unlabeled)
            kld_y_unlabeled = self.KLD(y_mu_unlabeled, y_log_sigma_unlabeled)
            unlabeled_loss = tf.reduce_mean(rec_loss_unlabeled + kld_z_unlabeled + kld_y_unlabeled)
            
        with tf.variable_scope("preventor_loss"):
            p_loss = 0.2*tf.reduce_mean(tf.square(y-anti_prediction))
            
        ################
        # Optimization #
        ################
        update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
        with tf.control_dependencies(update_ops):
            labeled_optim = tf.train.AdamOptimizer().minimize(labeled_loss)
            unlabeled_optim = tf.train.AdamOptimizer().minimize(unlabeled_loss)
            
            # Preventor
            # Define optimizers
            opt_p = tf.train.AdamOptimizer()
            p_vars = [v for v in tf.trainable_variables() if "preventor" in v.name]
            e_vars = [v for v in tf.trainable_variables() if "encoder" in v.name]
            grads_and_vars = opt_p.compute_gradients(p_loss, p_vars+e_vars)
            temp = []
            for grad, tvar in grads_and_vars:
                if "encoder" in tvar.name:
                    temp.append((-1*grad, tvar))
                else:
                    temp.append((grad, tvar))
            prevent_optim = opt_p.apply_gradients(temp, name="minimize_p_loss")
            
        #########################
        # Network for later use #
        #########################
        x_ = tf.placeholder(tf.float32, shape=[None, self.input_dim], name="x")
        # Building network q(y|x)
        y_mu_, y_log_sigma_ = self.encoder(x_,
                                           output_dim=self.prediction_dim,
                                           dims = self.regressordims,
                                           _is_train=False,
                                           scope="regressor",
                                           reuse=True)
            
        return dict(
            x_in_labeled = x_in_labeled,
            x_in_unlabeled = x_in_unlabeled,
            z_mu_labeled = z_mu_labeled,
            z_mu_unlabeled = z_mu_unlabeled,
            y = y,
            y_log_sigma_unlabeled = y_log_sigma_unlabeled,
            y_log_sigma_labeled = y_log_sigma_labeled,
            rec_loss_unlabeled = rec_loss_unlabeled,
            rec_loss_labeled = rec_loss_labeled,
            kld_z_unlabeled = kld_z_unlabeled,
            kld_y_unlabeled = kld_y_unlabeled,
            kld_labeled = kld_labeled,
            density_labeled = density_labeled,
            unlabeled_loss = unlabeled_loss,
            labeled_loss = labeled_loss,
            prevent_loss = p_loss,
            unlabeled_optim = unlabeled_optim,
            labeled_optim = labeled_optim,
            prevent_optim = prevent_optim,
            x_ = x_,
            y_mu_ = y_mu_,
            y_log_sigma_ = y_log_sigma_
        )
        
    ###########
    # Encoder #
    ###########
    def encoder(self, _input, _is_train, output_dim, dims, _fn=tf.nn.relu, scope="encoder", reuse=None):
        with tf.variable_scope(scope, reuse=reuse):
            net = _input
            for dim in dims:
                net = tf.contrib.slim.fully_connected(net, dim, activation_fn=tf.identity)
                if self.verbose: print net
                net = tf.contrib.layers.batch_norm(net, is_training=_is_train)
                net = _fn(net)
            
            mu = tf.contrib.slim.fully_connected(net, output_dim, activation_fn=tf.identity)
            log_sigma = tf.contrib.slim.fully_connected(net, output_dim, activation_fn=tf.identity)
            if self.verbose: print mu, log_sigma
            return mu, log_sigma
                
    ###########
    # Decoder #
    ###########
    def decoder(self, _input, _is_train, dims, _fn=tf.nn.relu, scope="decoder", reuse=None):
        with tf.variable_scope(scope, reuse=reuse):
            net = _input
            for dim in dims[::-1]:
                net = tf.contrib.slim.fully_connected(net, dim, activation_fn=tf.identity)
                if self.verbose: print net
                net = tf.contrib.layers.batch_norm(net, is_training=_is_train)
                net = _fn(net)
            
            output = tf.contrib.slim.fully_connected(net, self.input_dim, activation_fn=tf.sigmoid)
            if self.verbose: print output
            return output
        
    #############
    # Predictor #
    #############
    def preventor(self, _input, _is_train, _fn=tf.nn.relu, scope="preventor", reuse=None):
        with tf.variable_scope(scope, reuse=reuse):
            output = tf.contrib.slim.fully_connected(_input, self.prediction_dim, activation_fn=tf.identity)
            if self.verbose: print output
            return output
        
        
    ###########
    # Sampler # 
    ###########
    def sample(self, mu, log_sigma):
        with tf.variable_scope("sample"):
            eps = tf.random_normal(tf.shape(log_sigma))
            return mu + eps * tf.exp(log_sigma)
        
    #########################################
    # KL divergence of Gaussian from N(0,1) #
    #########################################
    def KLD(self, mu, log_sigma):
        with tf.name_scope("KLD"):
            return -0.5 * tf.reduce_sum(1 + 2 * log_sigma - mu**2 - tf.exp(2 * log_sigma), 1)
        
    #######################
    # Entropy of Gaussian #
    #######################
    def gaussian_entropy(self, mu, log_sigma):
        with tf.name_scope("gaussian_entropy"):
            return 0.5 * tf.reduce_sum(tf.log(2 * math.pi * math.e) + 2*log_sigma, 1)

    ########################
    # Gaussian log density #
    ########################
    def gaussian_log_density(self, x, mu, log_sigma):
        with tf.name_scope("gaussian_log_density"):
            c = - 0.5 * math.log(2 * math.pi)
            density = c - 2*log_sigma / 2 - tf.squared_difference(x, mu) / (2 * tf.exp(2 * log_sigma))
            return tf.reduce_sum(density, axis=-1)
    
    #######################
    # Binary Crossentropy #
    #######################
    def crossEntropy(self, pred, target, offset=1e-7):
        with tf.name_scope("BinearyXent"):
            pred_ = tf.clip_by_value(pred, offset, 1 - offset)
            return -tf.reduce_sum(target * tf.log(pred_) + (1 - target) * tf.log(1 - pred_), 1)
   
    ############
    # Training #
    ############
    def train(self, X_labeled, X_unlabeled, epochs, valid=None):
        e = 0
        start_e = self.e
        try:
            while e < epochs:
                for i in range(X_labeled.batch_num):
                    # Get data
                    x_labeled, y = X_labeled.next()
                    x_unlabeled, _ = X_unlabeled.next()

                    # Training with labeled data
                    feed_dict = {self.ops["x_in_labeled"]: x_labeled,
                                 self.ops["x_in_unlabeled"]: x_unlabeled,
                                 self.ops["y"]: y}
                    ops_to_run = [self.ops["y_log_sigma_labeled"],
                                  self.ops["z_mu_labeled"],
                                  self.ops["rec_loss_labeled"],
                                  self.ops["kld_labeled"],
                                  self.ops["density_labeled"],
                                  self.ops["labeled_loss"],
                                  self.ops["labeled_optim"]]
                    ls, mus_l, rec_l, kld_z_l, logdense_y_l, loss_l, _ = self.sesh.run(ops_to_run, feed_dict)
                    
                    # Run a preventor net
                    ops_to_run = [self.ops["prevent_loss"],
                                  self.ops["prevent_optim"]]
                    p_loss, _ = self.sesh.run(ops_to_run, feed_dict)

                    # Training with unlabeled data
                    ops_to_run = [self.ops["y_log_sigma_unlabeled"],
                                  self.ops["z_mu_unlabeled"],
                                  self.ops["rec_loss_unlabeled"],
                                  self.ops["kld_z_unlabeled"],
                                  self.ops["kld_y_unlabeled"],
                                  self.ops["unlabeled_loss"],
                                  self.ops["unlabeled_optim"]]
                    ls, mus_ul, rec_ul, kld_z_ul, kld_y_ul, loss_ul, _ = self.sesh.run(ops_to_run, feed_dict)

                    # Use Validation
                    if valid != None:
                        mu, sigma = self.predict(valid[0])
                        mse = np.mean(np.square(valid[1]-mu))
                    
                    self.record["mus"].append(mus_ul)
                    self.record["ys"].append(y)

                    # Track loss
                    self.loss["loss_l"].append(loss_l)
                    self.loss["rec_l"].append(np.mean(rec_l))
                    self.loss["kld_z_l"].append(np.mean(kld_z_l))
                    self.loss["logdense_y_l"].append(np.mean(logdense_y_l))
                    self.loss["loss_ul"].append(loss_ul)
                    self.loss["rec_ul"].append(np.mean(rec_ul))
                    self.loss["kld_z_ul"].append(np.mean(kld_z_ul))
                    self.loss["kld_y_ul"].append(np.mean(kld_y_ul))
                    self.loss["mse"].append(mse)

                    # Print loss
                    loss = loss_l+loss_ul
                    kld = np.mean(kld_z_l)+np.mean(kld_z_ul)
                    rec = np.mean(rec_l)+np.mean(rec_ul)
                    if valid!=None:
                        sys.stdout.write("\rEpoch: [%2d/%2d] loss: %.2f, kld: %.2f, rec: %.2f, logdensity(labeled): %.2f, kld(unlabeled): %.2f, mse: %.2f"
                                         %(self.e, start_e+epochs, loss, kld, rec, np.mean(logdense_y_l), np.mean(kld_y_ul), mse))
                    else:
                        sys.stdout.write("\rEpoch: [%2d/%2d] loss: %.2f, kld: %.2f, rec: %.2f, logdensity(labeled): %.2f, kld(unlabeled): %.2f"
                                         %(self.e, start_e+epochs, loss, kld, rec, np.mean(logdense_y_l), np.mean(kld_y_ul)))
                self.e+=1
                e+= 1 
            self.plotinfo()
        
        except(KeyboardInterrupt):
            self.plotinfo()
            
    def plotinfo(self, filename=""):
        plt.figure(figsize=(12,9))
        
        plt.subplot(331)
        plt.plot(self.loss["loss_l"])
        plt.xticks([],[])
        plt.title("Labeled loss")
        
        plt.subplot(332)
        plt.plot(self.loss["loss_ul"])
        plt.xticks([],[])
        plt.title("Unlabeled loss")
        
        plt.subplot(333)
        plt.plot(self.loss["rec_l"])
        plt.xticks([],[])
        plt.title("Labeled reconstruction loss")
        
        plt.subplot(334)
        plt.plot(self.loss["rec_ul"])
        plt.xticks([],[])
        plt.title("Unlabeled reconstruction loss")
        
        plt.subplot(335)
        plt.plot(self.loss["kld_z_l"])
        plt.xticks([],[])
        plt.title("Labeled Latent KLDivergence")
        
        plt.subplot(336)
        plt.plot(self.loss["kld_z_ul"])
        plt.xticks([],[])
        plt.title("Unlabeled Latent KLDivergence")
        
        plt.subplot(337)
        plt.plot(self.loss["logdense_y_l"])
        plt.title("Labeled LogDensity")
        
        plt.subplot(338)
        plt.plot(self.loss["kld_y_ul"])
        plt.title("Unlabeled y KLD")
        
        plt.subplot(339)
        plt.plot(self.loss["mse"])
        plt.title("Test MSE")
        plt.ylim(0, 2.0)
        
        if filename == "":
            plt.show()
        else:
            plt.savefig(filename)
            
    def save(self, folder):
        saver = tf.train.Saver(tf.all_variables())
        os.system("mkdir "+folder)
        saver.save(self.sesh, folder+"/model.ckpt")
        
    def load(self, folder):
        saver = tf.train.Saver(tf.all_variables())
        saver.restore(self.sesh, folder+"/model.ckpt")
            
    ##############
    # Prediction #
    ##############
    def predict(self, x=None):
        return self.sesh.run([self.ops["y_mu_"], self.ops["y_log_sigma_"]], {self.ops["x_"]: x})